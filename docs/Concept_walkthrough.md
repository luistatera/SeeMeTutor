# Execution Flow: App Start to Tutor Speaking

This document traces the complete execution flow of the application, focusing specifically on how audio is captured from the student, sent to the tutor (Gemini), and returned to the student for playback.

## 1. Application Start & Connection

When the student opens the frontend ([index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html)), the following initialization sequence occurs:

1. **DOM Load:** The browser loads [index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html).
2. **WebSocket Setup:** The [wsConnect()](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html#2263-2370) function is called immediately (or when the user initializes the session). This establishes a WebSocket connection to the backend (`ws://.../ws`).
3. **Backend Session Setup:** The backend ([main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py)) receives the WebSocket connection at the `@app.websocket("/ws")` endpoint.
4. **Gemini Connection:** The backend accepts the connection, initializes the internal state for the `session_id`, and kicks off an asynchronous task to connect to the Gemini API (`_connect_to_gemini`).

## 2. Student Audio Capture (Frontend)

The process of recording the student's voice starts when they interact with the microphone button.

1. **User Interaction:** The user clicks the microphone button (event listener attached to `micBtn`).
2. **Action Triggered:** The [toggleMic()](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html#3743-3752) function is invoked, which calls [startMic()](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html#3611-3708) if the microphone isn't already active.
3. **Media Access:** [startMic()](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html#3611-3708) uses `navigator.mediaDevices.getUserMedia({ audio: true })` to request microphone access.
4. **Audio Processing Network:**
   - An `AudioContext` is created at a sample rate of 16kHz.
   - The microphone stream is connected to an `AudioWorkletNode` that runs custom JavaScript (`pcm-processor.js`).
5. **Encoding and Transmission:**
   - The `pcm-processor.js` converts the raw audio bits into 16-bit PCM chunks.
   - These chunks are sent to the main frontend thread.
   - The frontend base64-encodes these chunks and sends them over the WebSocket connection:
     ```javascript
     ws.send(JSON.stringify({ type: "audio", data: base64EncodedPcm }));
     ```

## 3. Backend Routing (FastAPI)

The backend acts as a bridge, receiving the stream of audio from the frontend and forwarding it to the Gemini Live API.

1. **Receiving Audio Message:** Within the [websocket_endpoint](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#1743-2475) loop in [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py), the backend continually waits for messages from the frontend:
   ```python
   data = await websocket.receive_text()
   # Decodes JSON to a dict: msg
   ```
2. **Handling the "audio" event:** If `msg.get("type") == "audio"`, the backend decodes the base64 data back into raw bytes.
3. **Forwarding to Gemini:** The audio bytes are packaged into a Gemini `LiveClientRealtimeInput` object and streamed to the active Gemini Live session:
   ```python
   await session.send(input=types.LiveClientRealtimeInput(
       media_chunks=[types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")]
   ))
   ```

## 4. Tutor Processing & Audio Response (Backend)

While the backend processes incoming messages from the WebSocket, a parallel asynchronous task ([_iter_runner_events_with_retry](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#3297-3404)) continually reads events coming back from Gemini.

1. **Event Iterator Loop:** The backend spins on `async for event in session.receive():`.
2. **Extracting Content:** The loop inspects the returned events. It handles tool calls and text, but specifically looks for audio output:
3. **Identifying Audio Output:** If `event.content` and `event.content.parts` exist, it iterates through them. If it finds a part containing `inline_data` (audio), it processes it.
4. **Forwarding Output to Frontend:**
   - The backend retrieves the raw audio bytes: `audio_bytes = inline_data.data`.
   - It base64-encodes the bytes.
   - It sends them down the WebSocket to the frontend as a JSON message:
     ```python
     await _send_json(websocket, {"type": "audio", "data": encoded})
     ```
   - (The backend also logs latency metrics during this step, such as tracking the time-to-first-byte).

## 5. Tutor Speaks (Frontend Playback)

Finally, the frontend receives the tutor's synthesized voice and plays it back to the student.

1. **WebSocket OnMessage:** The frontend's `ws.onmessage` handler triggers when the backend sends the `{"type": "audio", ...}` message.
2. **Decoding:** It intercepts messages of type `"audio"`, decodes the base64 string back into raw bytes.
3. **Playback:** The decoded PCM audio buffer is pushed into the active playback scheduling system (e.g., scheduling a `BufferSourceNode` on the existing `AudioContext`), which causes the user's speakers to output the tutor's voice.
