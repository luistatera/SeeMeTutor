import json
import datetime
from google.cloud import firestore

db = firestore.Client(project="seeme-tutor")

session_id = "17fe4320-949f-45a0-b072-d8b483e6e5b5"
print(f"--- Fetching Progress logs for session {session_id} ---")

progress_ref = db.collection('sessions').document(session_id).collection('progress')
docs = progress_ref.order_by("timestamp").stream()

count = 0
for doc in docs:
    count += 1
    data = doc.to_dict()
    if 'timestamp' in data:
        data['timestamp_readable'] = datetime.datetime.fromtimestamp(data['timestamp']).isoformat()
    print(json.dumps(data, indent=2, default=str))

if count == 0:
    print("No progress logs found for this session.")
