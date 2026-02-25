Verification

 1. Run backend locally: cd pocs/10_screen_share_toggle && uvicorn main:app --reload --port 9000
 2. Open <http://localhost:9000> in browser (Chrome desktop recommended)
 3. Test each PRD criterion:

- M1: Click toggle button — source switches between Camera and Screen
- M2: Measure switch latency in metrics dashboard — must be < 500ms
- M3: Switch source while tutor is speaking — audio continues without gap
- M4: Switch to screen while idle — tutor acknowledges "I can see your screen now"
- M5: LIVE badge shows "LIVE - CAMERA" or "LIVE - SCREEN" correctly
- M6: Click "Stop Sharing" while in screen mode — reverts to camera
- M7: Deny screen share permission when browser prompts — stays on camera with error banner
- M8: Use browser's built-in "Stop sharing" overlay — app detects and reverts to camera
- M9: Share screen with visible work, stay silent — tutor proactively comments

 4. Rapid switch stress test:

- Toggle source 5 times in 10 seconds
- Verify: 0 WebSocket disconnects, 0 audio drops
- Check metrics: switches count = 5, avg latency < 500ms

 5. Screen readability test:

- Share a document or webpage with 12pt text
- Verify tutor can accurately read/reference the text content

 6. Check event log for source_switch events confirming all switches are logged
 7. Check logs/ directory for JSONL file with source_switch entries
