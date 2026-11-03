import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dataURL = source => `data:text/javascript;base64,${Buffer.from(source + '\n//# sourceURL=source-inspection-test-module.js').toString('base64')}`;
const coreURL = dataURL(await readFile(new URL('../src/robot_worlds/static/vendor/three.core.js', import.meta.url)));
const textureURL = dataURL(await readFile(new URL('../src/robot_worlds/static/scene-textures.js', import.meta.url)));
const THREE = await import(coreURL);
const source = (await readFile(new URL('../src/robot_worlds/static/source-scene-viewer.js', import.meta.url), 'utf8'))
  .replace("from 'three'", `from '${coreURL}'`).replace("from './scene-textures.js'", `from '${textureURL}'`);
const { loadSourceScene } = await import(dataURL(source));
const bitmap = (t, implementation) => {
  const prior = globalThis.createImageBitmap;
  globalThis.createImageBitmap = implementation;
  t.after(() => { globalThis.createImageBitmap = prior; });
};
function png(width = 2, height = 2) {
  const bytes = new Uint8Array(33); bytes.set([137,80,78,71,13,10,26,10]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13); view.setUint32(12, 0x49484452); view.setUint32(16, width); view.setUint32(20, height);
  return bytes; // Header fixture; decoding is mocked, never claimed to be an actual image.
}
function fixture() {
  const values = [.1,.2,.3, .4,.5,.6, .7,.8,.9, 1,0,0, 0,1,0, 0,0,1, .05,.1, .8,.2, .3,.95];
  const bytes = new Uint8Array(values.length*4), view = new DataView(bytes.buffer);
  values.forEach((v, i) => view.setFloat32(i*4, v, true));
  return { bytes, data: {
    schema_version: 1, geometry_file: 'geometry.bin', geometry_byte_length: bytes.length,
    bodies: [{id:0,name:'world',parent_id:null,position:[0,0,0],quaternion:[1,0,0,0],joints:[]},
      {id:1,name:'cabinet',parent_id:0,position:[2,3,0],quaternion:[1,0,0,0],joints:[]},
      {id:2,name:'door',parent_id:1,position:[1,0,0],quaternion:[1,0,0,0],joints:[
        {id:7,name:'hinge',type:'hinge',axis:[0,0,1],position:[-.5,0,0],reference:.25,range:[-.5,2],limited:true}]}],
    meshes:[{id:3,position:{offset:0,count:3,item_size:3},normal:{offset:36,count:3,item_size:3},uv:{offset:72,count:3,item_size:2}}],
    objects:[{id:9,name:'door_visual',body_id:2,type:'mesh',mesh_id:3,size:[1,1,1],position:[.5,0,0],quaternion:[1,0,0,0],material_id:4,rgba:[.5,.5,.5,1],effective_rgba:[.2,.4,.6,.8]}],
    materials:[{id:4,name:'original',rgba:[.1,.2,.3,1],specular:.3,shininess:.4,emission:.25,reflectance:0,
      texrepeat:[1,1],texuniform:false,texture_id:null,source_attributes:{},layers:[]}],textures:[], unsupported_features:[],
  }};
}
function network(t, bytes, image = png()) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, { signal }) => {
    assert.equal(signal.aborted, false); calls.push(url);
    return new Response(url.endsWith('/geometry.bin') ? bytes : image);
  });
  return calls;
}
function textured(data, type = '2d') {
  data.materials[0].texture_id = 5;
  data.textures.push({id:5,name:'original_image',type,path:`textures/${'a'.repeat(64)}.png`,width:2,height:2,
    byte_length:33,colorspace:'linear',original_attributes:{}});
}
function near(a,b) { assert(Math.abs(a-b) < 1e-6, `${a} != ${b}`); }

test('native triangle corners, source colors and body frames survive without bounds normalization', async t => {
  const {data,bytes} = fixture(), original = structuredClone(data); network(t, bytes);
  const loaded = await loadSourceScene(data, '/source/');
  t.after(loaded.dispose);
  assert.deepEqual(data, original);
  const mesh = loaded.meshNodes.get(9), geometry = mesh.geometry;
  near(geometry.attributes.position.getX(0), .1); near(geometry.attributes.position.getZ(2), .9);
  assert.deepEqual([...geometry.attributes.normal.array], [1,0,0,0,1,0,0,0,1]);
  near(geometry.attributes.uv.getY(2), .95);
  const world = new THREE.Vector3().fromBufferAttribute(geometry.attributes.position, 0).applyMatrix4(mesh.matrixWorld);
  near(world.x, 3.6); near(world.y, 3.2); near(world.z, .3);
  assert.equal(mesh.material.type, 'MeshPhongMaterial'); near(mesh.material.specular.r,.3);
  near(mesh.material.shininess,51.2); near(mesh.material.emissive.b,.15); near(mesh.material.opacity,.8);
  assert.equal(mesh.material.envMap, null); assert.equal(mesh.userData.object.id,9); assert.equal(mesh.userData.object.body_id,2);
  const shader = {fragmentShader:'#include <lights_phong_pars_fragment>'};
  mesh.material.onBeforeCompile(shader);
  assert.match(shader.fragmentShader, /return color;/);
  assert.match(shader.fragmentShader, /#define BRDF_Lambert sourceClassicDiffuse/);
  assert.match(shader.fragmentShader, /#undef BRDF_Lambert/);
  assert.deepEqual(loaded.warnings, []); assert.equal(loaded.joints.get(7).currentValue,.25);
});

test('hinge inspection uses source pivot and reference, affects descendants, and rejects limits', async t => {
  const {data,bytes} = fixture(); network(t, bytes);
  const loaded = await loadSourceScene(data, '/source/'); t.after(loaded.dispose);
  loaded.setJointValue(7, .25 + Math.PI/2);
  const mesh = loaded.meshNodes.get(9), point = new THREE.Vector3().applyMatrix4(mesh.matrixWorld);
  near(point.x,2.5); near(point.y,4); assert.equal(loaded.joints.get(7).supported,true);
  loaded.setJointValue(7,.25);
  near(mesh.getWorldPosition(new THREE.Vector3()).x,3.5);
  assert.throws(() => loaded.setJointValue(7,3), /outside limits/);
  assert.throws(() => loaded.setJointValue('7',.5), /Unsupported/);
  assert.throws(() => loaded.setJointValue(7,NaN), /Invalid/);
});

test('slide displacement follows the source body frame; multi-joint bodies remain explicitly unsupported', async t => {
  const {data,bytes} = fixture(); network(t, bytes);
  const body = data.bodies[2], s = Math.SQRT1_2;
  body.quaternion = [s,0,0,s]; body.joints[0] = {...body.joints[0],type:'slide',axis:[1,0,0],reference:.2,range:[0,1]};
  const loaded = await loadSourceScene(data,'/source/'); loaded.setJointValue(7,.7);
  near(loaded.bodies.get(2).position.x,1); near(loaded.bodies.get(2).position.y,.5); loaded.dispose();
  body.joints.push({...body.joints[0],id:8});
  const other = await loadSourceScene(data,'/source/'); t.after(other.dispose);
  assert.equal(other.joints.get(7).supported,false); assert(other.warnings.some(w=>w.joint_id===7));
  assert.throws(()=>other.setJointValue(7,.4),/Unsupported/);
});

test('free and ball references retain native pose without offering kinematic controls', async t => {
  const {data,bytes} = fixture(); network(t,bytes);
  data.bodies[2].joints = [{id:7,type:'free',reference:[1,0,0,1,0,0,0],inspection_supported:false}];
  const loaded = await loadSourceScene(data,'/source/'); t.after(loaded.dispose);
  assert.deepEqual(loaded.joints.get(7).currentValue,[1,0,0,1,0,0,0]);
  assert.equal(loaded.joints.get(7).supported,false);
  near(loaded.meshNodes.get(9).getWorldPosition(new THREE.Vector3()).x,3.5);
  assert.throws(()=>loaded.setJointValue(7,0),/Unsupported/);
  data.bodies[2].joints = [{id:7,type:'ball',reference:[1,0,0,0],inspection_supported:false}];
  const ball = await loadSourceScene(data,'/source/'); t.after(ball.dispose);
  assert.deepEqual(ball.joints.get(7).currentValue,[1,0,0,0]);
  assert.equal(ball.joints.get(7).supported,false);
});

test('2D source UV repeat/uniform/flips preserve shared image bytes and release them once', async t => {
  const {data,bytes} = fixture(); textured(data); const calls = network(t,bytes);
  data.materials[0].texrepeat = [2,3]; data.materials[0].texuniform = true;
  data.objects[0].size = [4,5,6]; data.textures[0].original_attributes = {hflip:'true',vflip:'true'};
  data.objects.push({...data.objects[0],id:10,name:'second_geom'});
  let closed=0, decoded=0;
  bitmap(t,async (_blob,options)=>{ decoded++; assert.equal(options.imageOrientation,'none'); return {width:2,height:2,close(){closed++;}}; });
  const loaded = await loadSourceScene(data,'/source/'), a=loaded.meshNodes.get(9), b=loaded.meshNodes.get(10);
  assert.equal(decoded,1); assert.equal(calls.length,2); assert.equal(a.material.map,b.material.map);
  assert.equal(a.material.map.flipY,false); assert.equal(a.material.map.colorSpace,THREE.NoColorSpace);
  near(a.geometry.attributes.uv.getX(0),1-.05*2*4); near(a.geometry.attributes.uv.getY(0),1-.1*3*5);
  assert.equal(data.meshes[0].uv.offset,72);
  let geoDisposed=0,matDisposed=0,texDisposed=0;
  a.geometry.addEventListener('dispose',()=>geoDisposed++); a.material.addEventListener('dispose',()=>matDisposed++);
  a.material.map.addEventListener('dispose',()=>texDisposed++);
  loaded.dispose(); loaded.dispose(); assert.equal(closed,1); assert.equal(geoDisposed,1); assert.equal(matDisposed,1); assert.equal(texDisposed,1);
});

test('source cube cabinet boxes use exact local-direction sampling, no reflective environment map', async t => {
  const {data,bytes} = fixture(); textured(data,'cube'); network(t,bytes);
  data.objects[0] = {...data.objects[0],type:'box',mesh_id:null,size:[2,3,4]};
  bitmap(t,async()=>({width:2,height:2,close(){}}));
  const loaded=await loadSourceScene(data,'/source/'); t.after(loaded.dispose);
  const mesh=loaded.meshNodes.get(9), shader={uniforms:{},vertexShader:'#include <common>\n#include <begin_vertex>',fragmentShader:'#include <common>\n#include <map_fragment>'};
  mesh.material.onBeforeCompile(shader);
  assert.deepEqual(shader.uniforms.sourceCubeScale.value.toArray(),[.5,1/3,.25]);
  assert.match(shader.fragmentShader,/sourceCubeUV/); assert.match(shader.fragmentShader,/texture2D\(map,/);
  assert.equal(mesh.material.envMap,null); assert.deepEqual(loaded.warnings,[]);
  const p=mesh.geometry.attributes.position,n=mesh.geometry.attributes.normal,uv=mesh.geometry.attributes.uv;
  for(let i=0;i<p.count;i++) {
    if(n.getZ(i)>.5){ near(uv.getX(i),(p.getX(i)/2+1)/2); near(uv.getY(i),(1-p.getY(i)/3)/2); }
  }
});

test('mapping omissions are explicit and do not silently claim original appearance', async t => {
  const {data,bytes} = fixture(); textured(data); network(t,bytes);
  data.objects[0] = {...data.objects[0],type:'sphere',mesh_id:null,size:[.3,0,0]};
  bitmap(t,async()=>({width:2,height:2,close(){}}));
  const loaded=await loadSourceScene(data,'/source/'); t.after(loaded.dispose);
  assert.equal(loaded.meshNodes.get(9).material.map,null);
  assert(loaded.warnings.some(w=>w.object_id===9&&/mapping not implemented/.test(w.reason)));
  assert.equal(loaded.group.userData.sourceScene.warnings,loaded.warnings);
});

test('bad binary spans and image headers fail before unsafe decoding; hierarchy cycles are refused', async t => {
  const {data,bytes} = fixture(); network(t,bytes); data.meshes[0].normal.offset=bytes.length;
  await assert.rejects(loadSourceScene(data,'/source/'),/span/);
  data.meshes[0].normal.offset=36; textured(data);
  bitmap(t,()=>assert.fail('mismatched image header must not be decoded'));
  network(t,bytes,png(8192,8192));
  await assert.rejects(loadSourceScene(data,'/source/'),/dimensions differ/);
  data.bodies[1].parent_id=2;
  await assert.rejects(loadSourceScene(data,'/source/'),/hierarchy/);
});

test('unsafe paths never fetch; an abort during image decoding closes the late bitmap', async t => {
  const {data,bytes} = fixture(); let calls=0;
  t.mock.method(globalThis,'fetch',async()=>{calls++; return new Response(bytes);});
  await assert.rejects(loadSourceScene({...data,geometry_file:'../geometry.bin'},'/source/'),/schema/);
  await assert.rejects(loadSourceScene(data,'/source/../private/'),/base/); assert.equal(calls,0);
  network(t,bytes); textured(data); let finish, began,closed=0;
  const started=new Promise(resolve=>{began=resolve;});
  bitmap(t,()=>{began(); return new Promise(resolve=>{finish=resolve;});});
  const controller=new AbortController(), pending=loadSourceScene(data,'/source/',{signal:controller.signal});
  await started; controller.abort(); finish({width:2,height:2,close(){closed++;}});
  await assert.rejects(pending,/cancelled/); assert.equal(closed,1);
});

test('compiled geom override precedence and declared binary encoding cannot silently change source appearance', async t => {
  const {data,bytes}=fixture(); network(t,bytes); delete data.objects[0].effective_rgba;
  const original=await loadSourceScene(data,'/source/');
  near(original.meshNodes.get(9).material.color.r,.1); original.dispose();
  data.objects[0].rgba=[.7,.6,.5,1];
  const override=await loadSourceScene(data,'/source/');
  near(override.meshNodes.get(9).material.color.r,.7); override.dispose();
  await assert.rejects(loadSourceScene({...data,geometry_byte_order:'big'},'/source/'),/encoding/);
});
