# POC 00 — Student Onboarding: Mini PRD

## Why This Matters

A great tutoring experience starts *before* the first class. If SeeMe Tutor simply drops a student into a blank call, the AI has no context. It doesn't know what to teach, how to speak, or what success looks like for this specific human.

The student report (POC 12) is only valuable if it measures progress against a meaningful benchmark. By capturing the student's **identity**, **current state**, and **end goal** upfront (e.g., "I am currently A2 German and want to pass the Telc B1 exam in 3 months"), the system can frame every session, every whiteboard note, and every final report around that north star.

---

## The Onboarding Goal (The "North Star" Profile)

The onboarding flow must output a structured `StudentProfile` that tells the AI exactly who it is talking to. The required data points are:

1. **Identity:** Name and age/grade level (e.g., University Student vs. 6th Grader). This dictates the AI's tone and pedagogical approach.
2. **Subject & Ultimate Goal:** What are we trying to achieve overall?
    * *Example:* Subject: "German Language", Goal: "Pass Telc B1 Certification".
3. **Current State / Baseline:** Where is the student struggling right now, or what is their starting proficiency?
    * *Example:* "I know basic present tense, but struggle with separable verbs and Dativ case."
4. **Learning Preferences (Optional but powerful):** Do they prefer strict immersion, or guided bilingual explanations?

---

## Architecture Flow

1. **Initial Visit:** The user arrives at the SeeMe Tutor web app without an active session or profile.
2. **The Intake Questionnaire (UI):** A clean, step-by-step wizard asks the student for the 4 core data points.
    * *Design Note:* This should feel like a premium onboarding experience (e.g., Duolingo or standard EdTech apps).
3. **Profile Generation:** The frontend submits this data to the backend.
4. **Firestore Persistence:** The backend creates a new document in the `students` Firestore collection.
    * It additionally creates an initial `Track` (from the "Ultimate Goal") and populates the first set of `Topics` based on the student's "Current State".
5. **Handoff to Live Session:** When the student clicks "Start Class", the backend injects this newly minted profile into the Gemini System Prompt, and the tutor knows *exactly* how to greet them and what to focus on.

---

## What "Done" Looks Like (Must-Haves)

* [ ] **M1 (Intake UI):** A functional frontend wizard that captures Name, Subject, Current Level, and Ultimate Goal.
* [ ] **M2 (Storage):** The submitted data successfully creates a well-formed `students/{student_id}` document in Firestore.
* [ ] **M3 (Track Generation):** The backend automatically translates the student's Ultimate Goal into a learning `Track` inside Firestore.
* [ ] **M4 (Context Injection):** The data successfully flows from Firestore into the `gemini_live.py` System Prompt so the AI references the Ultimate Goal within the first 30 seconds of the call.

---

## Connections to Other POCs

* **POC 12 (Final Report):** The report compares the day's "Session Objective" against the "Ultimate Goal" established here in POC 00.
* **POC 02 (Proactive Vision):** The AI can proactively ask to see the student's syllabus or study materials to align with the chosen goal.
* **POC 03 (Multilingual):** The student's chosen subject limits the language boundary rules.

---

## Success Metrics

| Metric | Target | Why it matters |
| :--- | :--- | :--- |
| **Onboarding Drop-off** | < 10% | The intake process must be friction-less. If it takes too long, users will bounce before experiencing the AI. |
| **Context Retention** | 100% | The AI must flawlessly remember the student's name, goal, and level without hallucinating other user profiles. |
