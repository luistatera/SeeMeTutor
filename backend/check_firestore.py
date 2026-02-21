import math
import os
from google.cloud import firestore

def check_progress():
    db = firestore.Client(project="seeme-tutor")
    progress_docs = list(db.collection_group("progress").stream())
    print(f"Found {len(progress_docs)} progress documents across all sessions.")
    for doc in progress_docs:
        print(f"Doc: {doc.reference.path}, Data: {doc.to_dict()}")

    sessions = list(db.collection("sessions").stream())
    for s in sessions:
        sub_colls = list(s.reference.collections())
        if sub_colls:
            print(f"Session {s.id} has subcollections: {[c.id for c in sub_colls]}")

if __name__ == "__main__":
    check_progress()
