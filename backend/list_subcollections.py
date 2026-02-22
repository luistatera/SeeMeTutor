import json
from google.cloud import firestore

db = firestore.Client(project="seeme-tutor")
session_id = "17fe4320-949f-45a0-b072-d8b483e6e5b5"
doc_ref = db.collection('sessions').document(session_id)
collections = doc_ref.collections()
col_names = [col.id for col in collections]
print(f"Collections for session {session_id}: {col_names}")
