# SeeMe Tutor: The 0-to-Hero Journey

## The Core Insight

The student's input varies wildly (a book page, a YouTube video, a PDF with 99 slides, a spoken question), but the **learning journey is always the same**. The tutor doesn't need separate modes. It needs one universal progression engine that turns any input into a clear path from "I don't know this" to "I own this."

---

## The Universal Progression: CAPTURE > CHUNK > CLIMB > CONFIRM

### Phase 1: CAPTURE (What are we working with?)

The tutor scopes the material and tells the student what's there.

| Input | What the tutor says |
|-------|-------------------|
| 1 book page via camera | "I see 3 exercises on this page" |
| YouTube transcript | "This video covers 4 key concepts" |
| PDF slide 12 of 99 | "This slide introduces possessive pronouns" |
| A spoken question | "So you want to understand resistance in physics" |

**Tool fired:** `set_session_phase("capture")`

### Phase 2: CHUNK (Break it into learnable pieces)

This is where the tutor earns its value. Any blob of content becomes 2-5 **micro-goals** written to the whiteboard. The student sees their path before they start.

| Input | Micro-goals generated |
|-------|----------------------|
| Book page with 3 exercises | Goal 1: Exercise A, Goal 2: Exercise B, Goal 3: Exercise C |
| YouTube video on photosynthesis | Goal 1: What is a chloroplast?, Goal 2: Light reactions, Goal 3: Calvin cycle, Goal 4: Why plants are green |
| PDF slide on possessive pronouns | Goal 1: mein/dein/sein forms, Goal 2: Fill-in-the-blank exercise, Goal 3: Build your own sentence |
| "Explain resistance in physics" | Goal 1: What is voltage?, Goal 2: What is current?, Goal 3: Ohm's Law |

**Tool fired:** `write_notes` pushes micro-goals as a checklist to the whiteboard, each with status `pending`.

### Phase 3: CLIMB (Master each micro-goal)

For each micro-goal, the tutor runs the mastery loop:

```
Teach/Guide (Socratic hints, not answers)
    > Student tries
        > Solve (student gets the right answer)
            > Explain ("Why does that work?")
                > Transfer (new problem, same concept)
                    > Mastered!
```

The whiteboard updates in real time: `pending > in_progress > mastered`

The student navigates by voice: "next," "skip this one," "go back to goal 2."

**Tools fired:** `update_note_status`, `log_progress`

### Phase 4: CONFIRM (Wrap up and connect)

When all micro-goals are done, or the session ends:

- Tutor summarizes what was mastered vs. what needs more work
- Connects the dots between micro-goals ("Now that you know voltage and current, Ohm's Law is just the relationship between them")
- Sets up the next session if applicable ("Next time we'll start at slide 13")

**Tools fired:** `log_progress` (session summary), `write_notes` (final recap)

---

## How It Looks on the Whiteboard

At any point during the session, the student sees something like:

```
Session: German A2, Slide 3

[x] Goal 1: mein/dein/sein forms        MASTERED
[>] Goal 2: Fill-in-the-blank exercise   IN PROGRESS
[ ] Goal 3: Build your own sentence      PENDING
```

This is the same visual regardless of whether the material came from a camera, a PDF, a video, or a question.

---

## Multi-Page / Long Content Strategy

For inputs that span many pages or segments (a 99-slide PDF, a 2-hour video):

- The tutor works on **one page/segment at a time**
- CAPTURE scopes just the current page/segment
- CHUNK creates micro-goals for that page/segment only
- When all micro-goals for this page are done, the tutor asks: "Ready for the next slide?"
- Cross-session memory tracks where the student left off

The student is never overwhelmed because they only see the micro-goals for what's in front of them right now.

---

## The Key Principle

**CHUNK is the magic step.** Turning any blob of content into a learnable sequence of 2-5 bite-sized goals is what makes "0 to hero" feel achievable instead of overwhelming. The scope of the input changes, but the pattern never does.
