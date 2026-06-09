import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    from moviepy.video.VideoClip import ColorClip
except ImportError:
    ColorClip = None

BASE_URL = "http://127.0.0.1:5501"
HOST = "127.0.0.1"
PORT = 5501
BACKEND_SCRIPT = Path(__file__).with_name('backend.py')


def encode_multipart(fields, files):
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    body = bytearray()

    for name, value in fields.items():
        body.extend(f'--{boundary}\r\n'.encode('utf-8'))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
        body.extend(str(value).encode('utf-8'))
        body.extend(b'\r\n')

    for name, (filename, content, content_type) in files.items():
        body.extend(f'--{boundary}\r\n'.encode('utf-8'))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode('utf-8')
        )
        body.extend(f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8'))
        body.extend(content)
        body.extend(b'\r\n')

    body.extend(f'--{boundary}--\r\n'.encode('utf-8'))
    content_type = f'multipart/form-data; boundary={boundary}'
    return content_type, bytes(body)


def fetch(url, method='GET', data=None, headers=None, timeout=30):
    headers = headers or {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, dict(response.getheaders()), response.read()


def wait_for_server(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, headers, body = fetch(f'{BASE_URL}/')
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def create_sample_video(path: Path):
    if ColorClip is None:
        raise RuntimeError('moviepy is required to generate the sample test video.')

    clip = ColorClip((320, 180), color=(0, 128, 255), duration=3)
    clip.write_videofile(
        str(path),
        fps=24,
        codec='libx264',
        audio=False,
        logger=None,
    )
    clip.close()


class BackendFlowTest:
    def __init__(self):
        self.backend_proc = None
        self.started_backend = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sample_path = Path(self.temp_dir.name) / 'sample.mp4'
        self.uploaded_filename = None

    def start_backend_if_needed(self):
        if self.is_port_open():
            return

        self.backend_proc = subprocess.Popen(
            [sys.executable, str(BACKEND_SCRIPT)],
            cwd=BACKEND_SCRIPT.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.started_backend = True
        if not wait_for_server(25):
            self.teardown()
            raise RuntimeError('Backend did not become available on port 5501')

    def is_port_open(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            try:
                sock.connect((HOST, PORT))
                return True
            except Exception:
                return False

    def teardown(self):
        if self.backend_proc and self.started_backend:
            self.backend_proc.terminate()
            try:
                self.backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.backend_proc.kill()
        self.temp_dir.cleanup()

    def run(self):
        try:
            create_sample_video(self.sample_path)
            self.start_backend_if_needed()
            self.test_health_check()
            self.test_upload_and_analyze()
            self.test_render()
            print('Backend flow test passed successfully.')
        finally:
            self.teardown()

    def test_health_check(self):
        status, headers, body = fetch(f'{BASE_URL}/')
        if status != 200:
            raise AssertionError(f'Health check failed with status {status}')

        data = json.loads(body)
        assert data.get('status') == 'online', 'Expected status=online'
        assert 'ai_enabled' in data, 'Expected ai_enabled key in health check response'
        print('Health check passed')

    def test_upload_and_analyze(self):
        with open(self.sample_path, 'rb') as f:
            file_bytes = f.read()

        content_type, body = encode_multipart(
            {},
            {'file': (self.sample_path.name, file_bytes, 'video/mp4')},
        )
        status, headers, response_body = fetch(
            f'{BASE_URL}/upload',
            method='POST',
            data=body,
            headers={'Content-Type': content_type},
        )
        if status != 200:
            raise AssertionError(f'Upload failed with status {status}')

        upload_data = json.loads(response_body)
        if upload_data.get('status') != 'uploaded':
            raise AssertionError('Upload response did not include status=uploaded')

        self.uploaded_filename = upload_data.get('filename')
        assert self.uploaded_filename, 'Upload response did not return a filename'
        print('Upload endpoint passed')

        analyze_body = urllib.parse.urlencode({'filename': self.uploaded_filename}).encode('utf-8')
        status, headers, response_body = fetch(
            f'{BASE_URL}/analyze',
            method='POST',
            data=analyze_body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        if status != 200:
            raise AssertionError(f'Analyze failed with status {status}')

        analyze_data = json.loads(response_body)
        assert 'clips' in analyze_data, 'Analyze response missing clips'
        assert 'duration' in analyze_data, 'Analyze response missing duration'
        assert analyze_data['duration'] > 0, 'Analyze response duration should be positive'
        self.clips = analyze_data['clips']
        print('Analyze endpoint passed')

    def test_render(self):
        if not self.uploaded_filename:
            raise AssertionError('No uploaded filename available for render test')

        start = 0.0
        end = 1.0
        if self.clips:
            clip = self.clips[0]
            start = float(clip.get('start', 0.0))
            end = float(clip.get('end', start + 1.0))

        fields = {
            'filename': self.uploaded_filename,
            'start': start,
            'end': end,
            'pan_x': 0.5,
            'caption': 'Test caption flow',
        }
        content_type, body = encode_multipart(fields, {})

        status, headers, response_body = fetch(
            f'{BASE_URL}/render',
            method='POST',
            data=body,
            headers={'Content-Type': content_type},
            timeout=120,
        )
        if status != 200:
            raise AssertionError(f'Render failed with status {status}')

        returned_type = headers.get('Content-Type') or headers.get('content-type', '')
        if 'video/mp4' not in returned_type:
            raise AssertionError(f'Unexpected Content-Type: {returned_type}')

        output_path = Path(self.temp_dir.name) / 'rendered_output.mp4'
        output_path.write_bytes(response_body)
        if output_path.stat().st_size < 1000:
            raise AssertionError('Rendered output appears too small')

        print('Render endpoint passed')


if __name__ == '__main__':
    if ColorClip is None:
        raise RuntimeError('moviepy is required for this test script. Install it in the virtual environment.')
    BackendFlowTest().run()
