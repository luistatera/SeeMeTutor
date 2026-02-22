import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import json

# Use application default credentials
cred = credentials.ApplicationDefault()
firebase_admin.initialize_app(cred)

db = firestore.client()

# Get the latest 5 sessions
sessions_ref = db.collection('sessions')
# Try ordering by started_at if it exists, otherwise list them all and take latest by somewhat id or another field
docs = sessions_ref.order_by("started_at", direction=firestore.Query.DESCENDING).limit(5).stream()

try:
    for doc in docs:
        print(f"Session ID: {doc.id}")
        data = doc.to_dict()
        print(json.dumps(data, indent=2, default=str))
        print("-" * 40)
except Exception as e:
    print(f"Error ordering by started_at: {e}")
    # Fallback to no ordering
    print("Falling back to listing some documents...")
    docs = sessions_ref.limit(5).stream()
    for doc in docs:
        print(f"Session ID: {doc.id}")
        data = doc.to_dict()
        print(json.dumps(data, indent=2, default=str))
        print("-" * 40)
