"""Serve the normal workbench plus a bounded, opt-in renderer diagnostic page.

Uses the production WorldViewer, same source assets and normal world API. The
extra /viewer-benchmark page is a development tool, not part of the shipped UI.
"""
import argparse
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse

from robot_worlds.server import create_app

PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Robot Worlds renderer measurement</title>
<script type="importmap">{"imports":{"three":"/static/vendor/three.module.js"}}</script>
<style>body{margin:0;background:#262626;color:#eee;font:14px system-ui;display:flex}
#stage{position:relative;width:684px;height:720px;flex-shrink:0}canvas{width:100%;height:100%}
#labels{position:absolute;inset:0;pointer-events:none}#labels>*{position:absolute;font-size:10px}
main{padding:20px;max-width:550px}pre{white-space:pre-wrap;font-size:12px}button{padding:8px}</style>
<div id="stage"><canvas tabindex="0"></canvas><div id="labels"></div></div>
<main><h1>Renderer measurement</h1><p>Production viewer, 684 × 720 CSS-pixel viewport.
90 orbit frames after source loading. Browser frame intervals and CPU submission
are measured; these are not GPU timer-query measurements.</p>
<button id="start" disabled>Measure 90 frames</button><pre id="result">Loading world…</pre></main>
<script type="module">
import {WorldViewer} from '/static/scene.js';
const output=document.querySelector('#result'), button=document.querySelector('#start');
const id=new URLSearchParams(location.search).get('world');
const started=performance.now();
let viewer;
function summary(values){const a=[...values].sort((a,b)=>a-b);return {samples:a.length,
 median:a[Math.floor(a.length*.5)],p95:a[Math.min(a.length-1,Math.floor(a.length*.95))],max:a.at(-1)}}
try {
 if(!id||!/^[a-f0-9]{20,64}$/.test(id))throw Error('Supply a saved world ID');
 const response=await fetch('/api/worlds/'+id);if(!response.ok)throw Error('World unavailable');
 const world=await response.json();
 viewer=new WorldViewer(document.querySelector('canvas'),document.querySelector('#labels'),()=>{});
 viewer.setSpec(world.spec,'/api/worlds/'+id+'/files/');
 const deadline=performance.now()+60000;
 while(!viewer.sourceBatched){if(performance.now()>deadline)throw Error('Source load timeout');await new Promise(r=>setTimeout(r,100));}
 const loaded={world_id:id,components:world.spec.objects.length,
  ready_seconds:(performance.now()-started)/1000,viewport_css:[684,720],
  pixel_ratio:viewer.renderer.getPixelRatio(),batching:viewer.sourceBatchStats,
  source_error_groups:viewer.assetErrorLabels.size,additional_source_errors:viewer.additionalAssetErrors,
  user_agent:navigator.userAgent};
 output.textContent=JSON.stringify(loaded,null,2);button.disabled=false;
 button.onclick=async()=>{
  button.disabled=true;
  const frame=[],submission=[];let previous;
  const original=viewer.renderer.render.bind(viewer.renderer);
  viewer.renderer.render=(...args)=>{const t=performance.now();original(...args);submission.push(performance.now()-t)};
  try{
   const target=viewer.controls.target.clone(),offset=viewer.camera.position.clone().sub(target);
   for(let i=0;i<90;i++){
    const now=await new Promise(requestAnimationFrame);if(previous!==undefined)frame.push(now-previous);previous=now;
    const angle=i*.008,c=Math.cos(angle),s=Math.sin(angle);
    viewer.camera.position.set(target.x+offset.x*c-offset.y*s,target.y+offset.x*s+offset.y*c,target.z+offset.z);
    viewer.camera.lookAt(target);viewer.applyCutaway();viewer.invalidate();
   }
   await new Promise(requestAnimationFrame);
   output.textContent=JSON.stringify({...loaded,frame_interval_ms:summary(frame),
    render_submission_ms:summary(submission),draw_calls:viewer.renderer.info.render.calls,
    triangles:viewer.renderer.info.render.triangles,
    gpu_resources:{...viewer.renderer.info.memory},
    note:'Warm orbit after loading. Frame intervals include browser scheduling. CPU submission is not GPU duration. No simulation or model calls.'},null,2);
  }finally{viewer.renderer.render=original;viewer.dispose();button.textContent='Finished · resources disposed';}
 };
}catch(error){viewer?.dispose();output.textContent=error.message;}
</script></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    app = create_app(args.data_dir)
    @app.get('/viewer-benchmark', response_class=HTMLResponse)
    def benchmark():
        return PAGE
    uvicorn.run(app, host='127.0.0.1', port=args.port)
