"""Loopback-only model fixture for manual browser acceptance; no remote requests.

Run this in one terminal, then run the normal server with:
OPENAI_API_KEY=local-test-only OPENAI_BASE_URL=http://127.0.0.1:8777/v1 robot-worlds serve --data-dir /tmp/robot-worlds-browser-check --port 8767
Use a separate data directory. These are mocked decisions and synthetic token
counts, with real generation materialization, physics, jobs, reporting and UI.
Control file values: ready, invalid_key, unavailable, slow. Never use a real key.
"""
import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from offline_household import prepared_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', required=True, type=Path)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log prompts, images or authorization headers.

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer local-test-only':
                self.reply(403, {'error': {'message': 'This fixture only accepts the documented dummy credential.'}})
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 12*1024*1024:
                self.reply(413, {'error': {'message': 'Invalid fixture request size'}})
                return
            request = json.loads(self.rfile.read(length))
            if self.path == '/v1/responses/input_tokens':
                self.reply(200, {'object': 'response.input_tokens', 'input_tokens': 1000})
                return
            if self.path != '/v1/responses':
                self.reply(404, {'error': {'message': 'Unsupported fixture endpoint'}})
                return
            mode = args.control.read_text().strip()
            if mode == 'invalid_key':
                self.reply(401, {'error': {'message': 'Mock invalid credential', 'type': 'invalid_request_error', 'code': 'invalid_api_key'}})
                return
            if mode == 'unavailable':
                self.reply(503, {'error': {'message': 'Mock provider unavailable', 'type': 'server_error'}})
                return
            time.sleep(12 if mode == 'slow' else 3)
            if request.get('tools'):
                payload = json.loads(request['input'][0]['content'][0]['text'])
                step = len(payload.get('history', []))
                actions = [('drive', {'distance': .08, 'turn': 0}),
                           ('gripper', {'hand': 'right', 'opening': .035}),
                           ('wait', {'seconds': .2}), ('finish', {})]
                name, arguments = actions[min(step, len(actions)-1)]
                output = [{'type': 'function_call', 'id': 'fc_mock', 'call_id': 'call_mock',
                           'name': name, 'arguments': json.dumps(arguments), 'status': 'completed'}]
            else:
                plan = prepared_plan()
                plan['name'] = 'Browser acceptance · mocked model'
                plan['description'] = 'Deterministic local model fixture; physical runtime and grading are real.'
                output = [{'type': 'message', 'id': 'msg_mock', 'role': 'assistant', 'status': 'completed',
                           'content': [{'type': 'output_text', 'text': json.dumps(plan), 'annotations': []}]}]
            self.reply(200, {'id': 'resp_mock_'+uuid.uuid4().hex, 'object': 'response',
                            'created_at': int(time.time()), 'status': 'completed',
                            'model': request['model'], 'output': output,
                            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
                                      'input_tokens_details': {'cached_tokens': 0}}})

    print('Mock provider listening only on 127.0.0.1:8777; no remote model requests.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8777), Handler).serve_forever()


if __name__ == '__main__':
    main()
