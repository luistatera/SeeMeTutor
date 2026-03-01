"""
Seed 3 demo student profiles into Firestore for the hackathon demo.

Usage:
    python seed_demo_profiles.py [--project PROJECT_ID] [--dry-run]

Each profile includes:
    - Student document with name, tutor preferences, and profile context
    - One learning track with 3-4 topics
    - Each topic has a context_query for Google Search pre-loading

Re-running is safe — it uses set(merge=True) so existing data is updated,
not overwritten destructively.
"""

import argparse
import asyncio
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "seeme-tutor")

# ---------------------------------------------------------------------------
# Demo profile definitions
# ---------------------------------------------------------------------------

PROFILES = [
    {
        "student_id": "luis-german",
        "student": {
            "name": "Luis",
            "active_track_id": "german-a2",
            "tutor_preferences": {
                "speech_pace": "normal",
                "explanation_length": "balanced",
                "directness": "balanced",
                "socratic_intensity": "medium",
                "encouragement_level": "medium",
            },
            "profile_context": {
                "learner_identity": "Adult learner, software engineer",
                "study_subject": "German A2",
                "class_name": "German A2 course",
                "institution_name": "",
                "study_context": "Preparing for telc A2 exam, practicing with textbook exercises",
                "resource_context": "Menschen A2 textbook",
            },
        },
        "tracks": [
            {
                "track_id": "german-a2",
                "track": {
                    "title": "German A2",
                    "goal": "Pass telc A2 exam with confident speaking and grammar",
                },
                "topics": [
                    {
                        "topic_id": "dative-case",
                        "title": "Dative Case (Dativ)",
                        "status": "in_progress",
                        "order_index": 1,
                        "context_query": "German A2 dative case articles rules exercises dem der den",
                    },
                    {
                        "topic_id": "perfekt-tense",
                        "title": "Perfekt Tense",
                        "status": "not_started",
                        "order_index": 2,
                        "context_query": "German A2 Perfekt tense haben sein past participle rules exercises",
                    },
                    {
                        "topic_id": "modal-verbs",
                        "title": "Modal Verbs",
                        "status": "not_started",
                        "order_index": 3,
                        "context_query": "German A2 modal verbs können müssen dürfen wollen sollen exercises",
                    },
                    {
                        "topic_id": "wechselpraepositionen",
                        "title": "Wechselpräpositionen",
                        "status": "not_started",
                        "order_index": 4,
                        "context_query": "German A2 Wechselpräpositionen two-way prepositions Akkusativ Dativ in an auf exercises",
                    },
                ],
            },
        ],
    },
    {
        "student_id": "sofia-math",
        "student": {
            "name": "Sofia",
            "active_track_id": "grade4-math-french",
            "tutor_preferences": {
                "speech_pace": "slow",
                "explanation_length": "short",
                "directness": "to_the_point",
                "socratic_intensity": "light",
                "encouragement_level": "high",
            },
            "profile_context": {
                "learner_identity": "9-year-old girl, 4th grade",
                "study_subject": "Math and French",
                "class_name": "CM1 (4th grade equivalent)",
                "institution_name": "",
                "study_context": "Bilingual household (Portuguese/French), homework help after school",
                "resource_context": "School worksheets and exercise books",
            },
        },
        "tracks": [
            {
                "track_id": "grade4-math-french",
                "track": {
                    "title": "Grade 4 Math & French",
                    "goal": "Build confidence in multiplication and basic French grammar",
                },
                "topics": [
                    {
                        "topic_id": "multiplication-tables",
                        "title": "Multiplication Tables",
                        "status": "in_progress",
                        "order_index": 1,
                        "context_query": "grade 4 multiplication tables word problems strategies for kids",
                    },
                    {
                        "topic_id": "fractions-intro",
                        "title": "Introduction to Fractions",
                        "status": "not_started",
                        "order_index": 2,
                        "context_query": "grade 4 fractions introduction comparing fractions visual models exercises",
                    },
                    {
                        "topic_id": "french-present-tense",
                        "title": "French Verb Conjugation (Présent)",
                        "status": "not_started",
                        "order_index": 3,
                        "context_query": "French present tense conjugation CM1 regular verbs er ir re exercises for kids",
                    },
                    {
                        "topic_id": "french-vocabulary-animals",
                        "title": "French Vocabulary — Animals",
                        "status": "not_started",
                        "order_index": 4,
                        "context_query": "French vocabulary animals CM1 les animaux word list exercises for kids",
                    },
                ],
            },
        ],
    },
    {
        "student_id": "ana-chemistry",
        "student": {
            "name": "Ana",
            "active_track_id": "general-chemistry-1",
            "tutor_preferences": {
                "speech_pace": "fast",
                "explanation_length": "detailed",
                "directness": "exploratory",
                "socratic_intensity": "high",
                "encouragement_level": "medium",
            },
            "profile_context": {
                "learner_identity": "18-year-old university freshman",
                "study_subject": "General Chemistry I",
                "class_name": "Química Geral I",
                "institution_name": "University",
                "study_context": "First-year university chemistry, preparing for midterm exams",
                "resource_context": "Chemistry: The Central Science (Brown, LeMay) textbook",
            },
        },
        "tracks": [
            {
                "track_id": "general-chemistry-1",
                "track": {
                    "title": "General Chemistry I",
                    "goal": "Master fundamentals for midterm: atomic structure, bonding, stoichiometry, acids/bases",
                },
                "topics": [
                    {
                        "topic_id": "atomic-structure",
                        "title": "Atomic Structure",
                        "status": "in_progress",
                        "order_index": 1,
                        "context_query": "university general chemistry atomic structure electron configuration quantum numbers orbitals exercises",
                    },
                    {
                        "topic_id": "chemical-bonding",
                        "title": "Chemical Bonding",
                        "status": "not_started",
                        "order_index": 2,
                        "context_query": "university general chemistry chemical bonding ionic covalent metallic Lewis structures VSEPR exercises",
                    },
                    {
                        "topic_id": "stoichiometry",
                        "title": "Stoichiometry",
                        "status": "not_started",
                        "order_index": 3,
                        "context_query": "university general chemistry stoichiometry mole concept balancing equations limiting reagent yield exercises",
                    },
                    {
                        "topic_id": "acid-base",
                        "title": "Acid-Base Chemistry",
                        "status": "not_started",
                        "order_index": 4,
                        "context_query": "university general chemistry acid base Bronsted Lowry pH calculations buffer solutions exercises",
                    },
                ],
            },
        ],
    },
]


async def seed_profiles(project_id: str, *, dry_run: bool = False):
    from google.cloud import firestore

    db = firestore.AsyncClient(project=project_id)
    logger.info("Connected to Firestore project: %s", project_id)

    now = time.time()

    for profile in PROFILES:
        student_id = profile["student_id"]
        student_data = dict(profile["student"])
        student_data["updated_at"] = now

        student_ref = db.collection("students").document(student_id)

        if dry_run:
            logger.info("[DRY-RUN] Would write student: %s — %s", student_id, student_data.get("name"))
        else:
            await student_ref.set(student_data, merge=True)
            logger.info("Wrote student: %s — %s", student_id, student_data.get("name"))

        for track_def in profile["tracks"]:
            track_id = track_def["track_id"]
            track_data = dict(track_def["track"])
            track_data["updated_at"] = now

            track_ref = student_ref.collection("tracks").document(track_id)

            if dry_run:
                logger.info("  [DRY-RUN] Would write track: %s — %s", track_id, track_data.get("title"))
            else:
                await track_ref.set(track_data, merge=True)
                logger.info("  Wrote track: %s — %s", track_id, track_data.get("title"))

            for topic_def in track_def["topics"]:
                topic_id = topic_def["topic_id"]
                topic_data = {
                    "title": topic_def["title"],
                    "status": topic_def["status"],
                    "order_index": topic_def["order_index"],
                    "context_query": topic_def["context_query"],
                    "updated_at": now,
                }

                topic_ref = track_ref.collection("topics").document(topic_id)

                if dry_run:
                    logger.info("    [DRY-RUN] Would write topic: %s — %s", topic_id, topic_data["title"])
                else:
                    await topic_ref.set(topic_data, merge=True)
                    logger.info("    Wrote topic: %s — %s", topic_id, topic_data["title"])

    try:
        db.close()
    except Exception:
        pass
    logger.info("Done. %d profiles seeded.", len(PROFILES))


def main():
    parser = argparse.ArgumentParser(description="Seed demo profiles into Firestore")
    parser.add_argument(
        "--project",
        default=GCP_PROJECT_ID,
        help=f"GCP project ID (default: {GCP_PROJECT_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without actually writing to Firestore",
    )
    args = parser.parse_args()

    asyncio.run(seed_profiles(args.project, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
