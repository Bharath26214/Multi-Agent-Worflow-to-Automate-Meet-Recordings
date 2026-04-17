let sessionId = null;
let sessionData = null;

const transcriptEl = document.getElementById("transcript");
const startBtn = document.getElementById("start-btn");
const draftsEl = document.getElementById("drafts");
const raiseBtn = document.getElementById("raise-btn");
const resultsEl = document.getElementById("results");

const sampleTranscript = `[00:00] John: Good morning everyone. Quick status sync for checkout and release prep.
[00:10] Priya: Auth middleware tests are done from QA side.
[00:22] John: Alex, finish Stripe API integration by 2026-04-20. High priority.
[00:36] Alex: Confirmed, I’ll deliver by 2026-04-20.
[00:48] John: Priya, create end-to-end checkout test cases by 2026-04-21. Medium priority.
[01:02] Priya: Sure, I'll take that.
[01:15] Mike: We still see webhook retries failing intermittently.
[01:24] John: Mike, fix the webhook retry bug and push patch by 2026-04-19. High priority.
[01:40] Mike: Done, I’ll handle it.
[01:52] John: Someone update the API docs soon.
[02:03] John: Let's also improve monitoring maybe this week.
[02:14] John: Alex, cleanup payment endpoint docs by 2026-04-22. Low priority.
[02:27] Alex: Got it.
[02:35] John: Great, let's close.`;

if (!transcriptEl.value) {
  transcriptEl.value = sampleTranscript;
}

function render() {
  if (!sessionData) {
    draftsEl.innerHTML = "<p>No session yet.</p>";
    raiseBtn.disabled = true;
    return;
  }

  const drafts = sessionData.draft_tickets || [];
  if (!drafts.length) {
    draftsEl.innerHTML = "<p>No draft tickets left.</p>";
  } else {
    draftsEl.innerHTML = drafts
      .map(
        (draft) => `
          <div class="draft">
            <strong>${draft.event_id}</strong> - ${draft.summary}
            <div>Reasons: ${draft.reasons && draft.reasons.length ? draft.reasons.join(", ") : "none"}</div>
            <div class="row">
              <input id="edit-${draft.event_id}" type="text" placeholder="Edit instruction (e.g., assign to Priya with due date 2026-04-20)" />
              <button data-action="edit" data-event="${draft.event_id}">Edit</button>
              <button data-action="approve" data-event="${draft.event_id}">Approve</button>
              <button data-action="reject" data-event="${draft.event_id}">Reject</button>
            </div>
          </div>
        `
      )
      .join("");
  }

  raiseBtn.disabled = false;
}

async function refreshSession() {
  if (!sessionId) return;
  const res = await fetch(`/api/sessions/${sessionId}`);
  if (!res.ok) throw new Error("Failed to refresh session");
  sessionData = await res.json();
  render();
}

startBtn.addEventListener("click", async () => {
  resultsEl.textContent = "";
  const transcript = transcriptEl.value.trim();
  if (!transcript) return;
  startBtn.disabled = true;
  try {
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    sessionId = data.session_id;
    sessionData = data;
    render();
  } catch (e) {
    resultsEl.textContent = String(e);
  } finally {
    startBtn.disabled = false;
  }
});

draftsEl.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;
  const action = target.dataset.action;
  const eventId = target.dataset.event;
  if (!action || !eventId || !sessionId) return;

  target.disabled = true;
  try {
    let endpoint = `/api/sessions/${sessionId}/drafts/${eventId}/${action}`;
    let options = { method: "POST", headers: { "Content-Type": "application/json" } };
    if (action === "edit") {
      const input = document.getElementById(`edit-${eventId}`);
      const instruction = input && input.value ? input.value.trim() : "";
      if (!instruction) throw new Error("Edit instruction is required.");
      options.body = JSON.stringify({ instruction });
    }

    const res = await fetch(endpoint, options);
    if (!res.ok) throw new Error(await res.text());
    sessionData = await res.json();
    render();
  } catch (e) {
    resultsEl.textContent = String(e);
  } finally {
    target.disabled = false;
  }
});

raiseBtn.addEventListener("click", async () => {
  if (!sessionId) return;
  raiseBtn.disabled = true;
  try {
    const res = await fetch(`/api/sessions/${sessionId}/raise`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    resultsEl.textContent = JSON.stringify(data.jira_create_results, null, 2);
    await refreshSession();
  } catch (e) {
    resultsEl.textContent = String(e);
  } finally {
    raiseBtn.disabled = false;
  }
});

render();

