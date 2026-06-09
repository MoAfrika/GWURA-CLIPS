# GWURA CLIPS Backend Behavioral Test Plan

## Purpose
Verify the full frontend/backend API flow end-to-end for `upload`, `analyze`, and `render`.

## Preconditions
- The backend dependencies are installed in the project virtual environment.
- The backend port is `5501` and either:
  - no server is running and the test script will start `backend.py`, or
  - a backend server is already listening on `http://127.0.0.1:5501`.

## Test Scenarios

### 1. Health Check
- Call `GET /`
- Expect HTTP 200
- Expect JSON with keys: `status`, `ai_enabled`
- Verify `status` equals `online`

### 2. Upload Endpoint
- POST a small generated video file to `/upload`
- Expect HTTP 200
- Expect JSON with keys: `filename`, `original_filename`, `status`
- Verify `status` equals `uploaded`
- Verify the uploaded filename contains a UUID prefix and the original name

### 3. Analyze Endpoint
- POST `filename` returned from upload to `/analyze`
- Expect HTTP 200
- Expect JSON with keys: `clips`, `duration`
- Verify `clips` is a list
- If the uploaded file has no audio, the endpoint may return `[]`, and duration should still be positive

### 4. Render Endpoint
- POST `filename`, `start`, `end`, `pan_x`, and `caption` to `/render`
- Expect HTTP 200
- Expect a binary video body with `Content-Type: video/mp4`
- Save the response body to disk and verify the file is non-empty

## Behavioral Checks
- Confirm backend returns valid JSON payloads for API calls
- Confirm the upload filename is safe and collision-resistant
- Confirm the backend handles missing audio without crashing
- Confirm the render endpoint delivers a downloadable MP4 file

## Execution
Run the test script from the project root:

```bash
python test_backend_flow.py
```

If tests fail because port `5501` is already in use, stop the conflicting process or run the backend manually on a free port and update the script accordingly.
