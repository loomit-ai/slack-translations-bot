"use strict";

const $ = (id) => document.getElementById(id);

const LOST = {
  title: "The page lost contact with its server",
  advice: "Check that the terminal running the translation page is still open, then reload this page.",
};
const NO_FILE = {
  title: "No file chosen",
  advice: "Choose a .txt or .csv file, then press Translate file.",
};

let info = null;
let job = null;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, body) {
  const options =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    return { ok: false, status: 0, data: { problem: LOST } };
  }
  try {
    return { ok: response.ok, status: response.status, data: await response.json() };
  } catch {
    return { ok: false, status: response.status, data: { problem: LOST } };
  }
}

function showProblem(box, problem) {
  box.replaceChildren(el("strong", { text: problem.title }), el("p", { text: problem.advice }));
  box.hidden = false;
}

function targetName(code) {
  const found = info.targets.find((target) => target.code === code);
  return found ? `${found.name} (${code})` : code;
}

function fillTargets(select) {
  select.replaceChildren(
    ...info.targets.map((target) =>
      el("option", { value: target.code, text: `${target.name} (${target.code})` }),
    ),
  );
  select.value = info.read_language;
}

function showTab(name) {
  for (const tab of ["one", "file"]) {
    const selected = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(selected));
    $(`tab-${tab}`).tabIndex = selected ? 0 : -1;
    $(`panel-${tab}`).hidden = !selected;
  }
}

function describeCheck(view) {
  return view.slack_tokens.ok
    ? "Slack mentions, links, emoji and code: all kept."
    : `Slack mention, link, emoji or code changed (${view.slack_tokens.summary}).`;
}

function describeDetected(view) {
  if (view.detected) {
    const { name, code, confidence } = view.detected;
    return `Detected: ${name} (${code}), confidence ${confidence.toFixed(2)} · Bot: ${view.decision}`;
  }
  return view.decision ? `Detected: nothing certain · Bot: ${view.decision}` : "";
}

let ticker = null;

function setWorking(on) {
  $("one-go").disabled = on;
  $("one-working").hidden = !on;
  clearInterval(ticker);
  if (!on) return;
  $("one-idle").hidden = true;
  $("one-result").hidden = true;
  $("one-problem").hidden = true;
  $("one-working-text").textContent =
    `Translating with ${info.model}… gives up after ${info.timeout} s.`;
  const started = performance.now();
  const tick = () => {
    $("one-elapsed").textContent = `${((performance.now() - started) / 1000).toFixed(0)} s`;
  };
  tick();
  ticker = setInterval(tick, 500);
}

function renderOne(view) {
  $("one-translation").textContent = view.translation;
  $("one-translation").lang = view.target;
  const facts = [`${view.seconds.toFixed(2)} s`, `into ${targetName(view.target)}`];
  if (view.tokens) facts.push(`${view.tokens.prompt} in · ${view.tokens.completion} out tokens`);
  $("one-facts").replaceChildren(...facts.map((text) => el("li", { text })));
  $("one-detected").textContent = describeDetected(view);
  $("one-detected").hidden = !$("one-detected").textContent;
  $("one-check").textContent = describeCheck(view);
  $("one-check").className = `check ${view.slack_tokens.ok ? "ok" : "flag"}`;
  $("one-result").hidden = false;
}

async function translateOne(event) {
  event.preventDefault();
  if ($("one-go").disabled) return;
  setWorking(true);
  const { data } = await api("/api/translate", {
    text: $("text").value,
    target: $("target-one").value,
  });
  setWorking(false);
  if (data.problem) {
    showProblem($("one-problem"), data.problem);
    if (data.problem.kind === "empty") $("text").focus();
    return;
  }
  renderOne(data);
}

function setRunning(running) {
  $("file-go").hidden = running;
  $("file-stop").hidden = !running;
  $("file").disabled = running;
  $("target-file").disabled = running;
  $("save").disabled = running || !job || job.count === 0;
}

function countsText(summary) {
  const parts = [];
  if (summary.failed) parts.push(`${summary.failed} failed`);
  if (summary.flagged) parts.push(`${summary.flagged} with a Slack flag`);
  return parts;
}

function summaryText(summary) {
  const parts = [`${summary.translated} of ${summary.done} translated`, ...countsText(summary)];
  if (summary.done) parts.push(`average ${summary.average_seconds.toFixed(1)} s`);
  return parts.join(" · ");
}

function addFailure(problem) {
  if (job.problems.has(problem.kind)) return;
  job.problems.add(problem.kind);
  $("file-failures").append(
    el("li", {}, el("strong", { text: `${problem.title}. ` }), document.createTextNode(problem.advice)),
  );
}

function markChanged() {
  job.dirty = true;
  $("save-status").textContent = "";
}

function addRow(row) {
  const number = row.index + 1;
  job.marks[row.index] = { rating: "", note: "" };
  const rating = el(
    "select",
    {
      "aria-label": `Rating for message ${number}`,
      onchange: (event) => {
        job.marks[row.index].rating = event.target.value;
        markChanged();
      },
    },
    el("option", { value: "", text: "Not rated" }),
    ...info.ratings.map((value) =>
      el("option", { value, text: value.charAt(0).toUpperCase() + value.slice(1) }),
    ),
  );
  const note = el("input", {
    type: "text",
    maxlength: "5000",
    "aria-label": `Note for message ${number}`,
    oninput: (event) => {
      job.marks[row.index].note = event.target.value;
      markChanged();
    },
  });

  let translation;
  let checks;
  if (row.ok) {
    translation = el("td", { class: "text", dir: "auto", lang: row.target, text: row.translation });
    checks = el("td", { class: "checks" }, el("div", { text: `${row.seconds.toFixed(2)} s` }));
    const detected = describeDetected(row);
    if (detected) checks.append(el("div", { text: detected }));
    if (!row.slack_tokens.ok) {
      checks.append(el("span", { class: "flag", text: `Slack token flag: ${row.slack_tokens.summary}` }));
    }
  } else {
    translation = el(
      "td",
      { class: "failed" },
      el("strong", { text: row.problem.title }),
      el("span", { text: "Not translated" }),
    );
    checks = el("td", { class: "checks" }, el("div", { text: `${row.seconds.toFixed(2)} s` }));
    addFailure(row.problem);
  }

  $("rows").append(
    el(
      "tr",
      {},
      el("th", { scope: "row", class: "num", text: String(number) }),
      el("td", { class: "text", dir: "auto", text: row.source }),
      translation,
      checks,
      el("td", { class: "mark" }, rating),
      el("td", { class: "note" }, note),
    ),
  );
  job.count += 1;
  $("table-wrap").hidden = false;
}

function updateProgress(view) {
  $("progress").max = Math.max(view.total, 1);
  $("progress").value = view.summary.done;
  $("progress-text").textContent = [`${view.summary.done} of ${view.total}`, ...countsText(view.summary)].join(" · ");
}

function finish(view) {
  setRunning(false);
  const how = { done: "Done", stopped: "Stopped", failed: "The run stopped unexpectedly" }[view.state];
  $("file-status").textContent = `${how}: ${summaryText(view.summary)}. Rate each message, then save the results.`;
  if (view.saved_to) $("save-status").textContent = `Saved to ${view.saved_to}`;
}

async function follow() {
  while (job) {
    const { ok, data } = await api(`/api/job?after=${job.count}`);
    if (!ok || !data.job || data.job.id !== job.id) {
      if (!ok) showProblem($("file-problem"), data.problem ?? LOST);
      setRunning(false);
      return;
    }
    const view = data.job;
    for (const row of view.rows) addRow(row);
    updateProgress(view);
    if (view.state !== "running") {
      finish(view);
      return;
    }
    await sleep(500);
  }
}

function newJob(id, name, adopted) {
  job = { id, count: 0, marks: [], dirty: false, problems: new Set() };
  $("rows").replaceChildren();
  $("table-wrap").hidden = true;
  $("file-failures").replaceChildren();
  $("file-problem").hidden = true;
  $("save-status").textContent = "";
  $("progress").value = 0;
  $("progress-text").textContent = "";
  $("file-results").hidden = false;
  $("file-status").textContent = adopted
    ? `Showing the last run of ${name}. Ratings made before the page was reloaded are not kept.`
    : `Translating ${name} with ${info.model}…`;
  setRunning(true);
  return follow();
}

async function translateFile(event) {
  event.preventDefault();
  if ($("file-go").disabled) return;
  const file = $("file").files[0];
  const box = $("file-problem");
  if (!file) {
    showProblem(box, NO_FILE);
    $("file").focus();
    return;
  }
  if (file.size > info.max_file_bytes) {
    showProblem(box, {
      title: "That file is too large",
      advice: `Keep a file under ${Math.round(info.max_file_bytes / 1048576)} MB: split it and translate the parts one after the other.`,
    });
    return;
  }
  if (job?.dirty && !window.confirm("Start over? The ratings and notes you have not saved will be lost.")) {
    return;
  }
  const content = await file.text();
  const { ok, data } = await api("/api/file", {
    name: file.name,
    content,
    target: $("target-file").value,
  });
  if (!ok) {
    showProblem(box, data.problem ?? LOST);
    return;
  }
  newJob(data.id, file.name, false);
}

async function stopFile() {
  if (!job) return;
  $("file-stop").disabled = true;
  await api("/api/job/stop", { id: job.id });
  $("file-stop").disabled = false;
}

async function saveResults() {
  if (!job) return;
  $("save").disabled = true;
  const { ok, data } = await api("/api/job/save", { id: job.id, marks: job.marks });
  $("save").disabled = false;
  if (!ok) {
    showProblem($("file-problem"), data.problem ?? LOST);
    return;
  }
  $("file-problem").hidden = true;
  job.dirty = false;
  $("save-status").textContent = `Saved to ${data.path}. Saving again replaces this file.`;
}

function wireTabs() {
  const tabs = [$("tab-one"), $("tab-file")];
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showTab(index === 0 ? "one" : "file"));
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      next.click();
      next.focus();
    });
  });
}

async function adoptLastJob() {
  const { ok, data } = await api("/api/job?after=0");
  if (!ok || !data.job) return;
  showTab("file");
  $("target-file").value = data.job.target;
  await newJob(data.job.id, data.job.sample, true);
}

async function start() {
  wireTabs();
  const loaded = await api("/api/info");
  if (!loaded.ok) {
    showProblem($("page-problem"), loaded.data.problem ?? LOST);
    return;
  }
  info = loaded.data;
  $("model").textContent = info.model;
  $("endpoint").textContent = info.endpoint;
  $("where").textContent = `Text ${info.where}`;
  $("where").classList.toggle("remote", !info.local);
  fillTargets($("target-one"));
  fillTargets($("target-file"));

  $("one-form").addEventListener("submit", translateOne);
  $("text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("one-form").requestSubmit();
  });
  $("file-form").addEventListener("submit", translateFile);
  $("file-stop").addEventListener("click", stopFile);
  $("save").addEventListener("click", saveResults);
  window.addEventListener("beforeunload", (event) => {
    if (job?.dirty) event.preventDefault();
  });
  $("text").focus();
  await adoptLastJob();
}

start();
