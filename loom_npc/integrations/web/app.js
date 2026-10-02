"use strict";

// The browser is a debugger and a player interface. Only the server builds NPC context.
const $ = (selector) => document.querySelector(selector);
const ui = { world: null, traces: [], scenario: null, selectedId: "mara", traceId: null, busy: false, tab: "knowledge" };
const locationPoints = { square: [51, 56], inn: [29, 44], tower: [75, 47] };
const palette = {
  mara: { coat: "#648a84", light: "#adc3aa", hair: "#645954", skin: "#e7c9a2" },
  ivo: { coat: "#ae8c57", light: "#d4ba82", hair: "#806c53", skin: "#edd0a7" },
  orin: { coat: "#5c778b", light: "#94acb5", hair: "#4e6570", skin: "#e3c29b" },
  player: { coat: "#c2a35b", light: "#ead599", hair: "#736d55", skin: "#edcba0" },
};
const statusLabels = { executed: "已执行", rejected: "规则拒绝", parse_error: "解析失败", model_error: "模型失败", execution_error: "执行失败" };
const factNames = { greeting: "自我介绍", town: "小镇地理", letter_request: "等待的来信", lighthouse_secret: "灯塔的秘密", smuggler_route: "退潮时的小路", guard_duty: "守塔规则" };
const actionNames = { speak: "交谈", give: "交付物品", move: "移动" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function replaceChildren(selector, nodes) { $(selector).replaceChildren(...nodes); }
function actorName(id) { return ui.world?.actors[id]?.name || id || "角色"; }
function locationName(id) { return ui.world?.locations[id]?.name || id || "未知地点"; }
function itemName(id) { return ui.world?.items[id]?.name || id; }
function pretty(value) { return typeof value === "string" ? value : JSON.stringify(value, null, 2); }

function normalizeState(data) {
  if (!data.world || !data.world.actors || !Array.isArray(data.traces)) throw new Error("世界数据不完整，请重置后重试。");
  if (!data.scenario || !["mock", "deepseek"].includes(data.scenario.provider)) throw new Error("运行模型信息不完整，请刷新页面重试。");
  return { world: data.world, traces: data.traces, scenario: data.scenario };
}

function showNotice(message, error = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = !message;
}

function setBusy(value, label = "处理中…") {
  ui.busy = value;
  document.querySelectorAll("#conversation-form button, #reset-button, #eval-button, [data-scenario], .map-character:not(.player)").forEach((button) => { button.disabled = value; });
  $("#conversation-input").disabled = value;
  $("#replay-button").disabled = value || ui.traces.length === 0;
  $("#export-button").disabled = value || ui.traces.length === 0;
  $("#send-button").setAttribute("aria-busy", String(value));
  $("#send-button-label").textContent = value ? label : "发送";
}

async function request(path, body, asText = false) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { message = (await response.json()).error || message; } catch { /* Keep the HTTP status if the response is not JSON. */ }
    throw new Error(message);
  }
  return asText ? response.text() : response.json();
}

async function runOperation(operation, pendingMessage = "", pendingLabel = "处理中…") {
  if (ui.busy) return;
  setBusy(true, pendingLabel);
  showNotice(pendingMessage);
  try { await operation(); }
  catch (error) { showNotice(error.message === "Failed to fetch" ? "无法连接本地运行时。请确认 Demo 服务仍在运行，再刷新页面。" : error.message, true); }
  finally { setBusy(false); }
}

function applyState(data) {
  const state = normalizeState(data);
  ui.world = state.world;
  ui.traces = state.traces;
  ui.scenario = state.scenario;
  if (!ui.world.actors[ui.selectedId]) ui.selectedId = Object.keys(ui.world.actors).find((id) => id !== "player") || "player";
  if (data.trace) ui.traceId = data.trace.id;
  else if (!ui.traces.some((trace) => trace.id === ui.traceId)) ui.traceId = ui.traces.at(-1)?.id || null;
  render();
}

function renderModel() {
  const online = ui.scenario.provider === "deepseek";
  const provider = online ? "DeepSeek" : "Mock";
  const indicator = $(".model-indicator");
  indicator.dataset.provider = ui.scenario.provider;
  indicator.replaceChildren(el("span"), document.createTextNode(online ? "DeepSeek · 在线" : "Mock · 离线运行"));
  indicator.title = `当前模型：${ui.scenario.model}`;
  $("#runtime-footer").textContent = online ? `本地运行时 · DeepSeek 在线 · ${ui.scenario.model}` : "本地原型 · 确定性 Mock · 无需 API Key";
  $("#model-note").textContent = online
    ? "文本输入、问候、询问秘密与索要钥匙由 DeepSeek 决策；交信、移动与知识边界测试直接提交固定行动。输入与角色可见上下文会发至 DeepSeek，密钥仅由本地服务持有。"
    : "文本输入、问候、询问秘密与索要钥匙由本地 Mock 决策；交信、移动与知识边界测试直接提交固定行动，均不调用外部模型。";
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    const usesModel = ["greet", "secret", "key"].includes(button.dataset.scenario);
    button.title = usesModel ? `使用 ${provider} 生成行动，再交由世界规则校验` : "直接提交固定行动，由世界规则校验，不调用模型";
  });
}

function characterFigure(id) {
  const p = palette[id] || palette.mara;
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 32 44");
  svg.setAttribute("class", "character-figure");
  svg.setAttribute("aria-hidden", "true");
  const shape = (tag, attrs) => {
    const node = document.createElementNS(namespace, tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
    svg.append(node);
  };
  shape("ellipse", { cx: 16, cy: 41, rx: 10, ry: 2.5, fill: "#4e6a6420" });
  shape("path", { d: "M12 30v10m8-10v10", stroke: "#526965", "stroke-width": 4, "stroke-linecap": "round" });
  shape("path", { d: "M9 17Q16 12 23 17l4 16H5Z", fill: p.coat });
  shape("path", { d: "M16 16 10 33h12Z", fill: p.light, opacity: .65 });
  shape("path", { d: "m10 19-5 10m18-10 5 10", stroke: p.coat, "stroke-width": 4, "stroke-linecap": "round" });
  shape("ellipse", { cx: 16, cy: 10, rx: 6, ry: 7, fill: p.skin });
  shape("path", { d: "M9 11V7C9-1 24-1 23 8v5l-4-6-9 4Z", fill: p.hair });
  if (id === "mara") shape("path", { d: "m9 8-1 8 4 2-1-8Z", fill: p.hair });
  if (id === "ivo") shape("path", { d: "m7 6 4-5 11 1 3 6Z", fill: "#9d8e69" });
  if (id === "orin") shape("path", { d: "M8 7Q16-6 24 7l1 3H7Z", fill: "#748b94" });
  if (id === "player") shape("path", { d: "m7 18 18 1-5 7-9-2Z", fill: "#e7d18b" });
  shape("circle", { cx: 18, cy: 10, r: .8, fill: "#5f6158" });
  return svg;
}

function renderMap() {
  const actors = Object.values(ui.world.actors);
  const nodes = actors.map((actor) => {
    const isPlayer = actor.id === "player";
    const node = el(isPlayer ? "div" : "button", `map-character ${isPlayer ? "player" : ""} ${actor.id === ui.selectedId ? "selected" : ""}`);
    const [x, y] = locationPoints[actor.location] || locationPoints.square;
    const sameLocation = actors.filter((other) => other.location === actor.location && other.id !== "player");
    const offset = isPlayer ? [7, 3] : [sameLocation.findIndex((other) => other.id === actor.id) * 5, 0];
    node.style.left = `${x + offset[0]}%`;
    node.style.top = `${y + offset[1]}%`;
    node.append(characterFigure(actor.id), el("span", "character-label", isPlayer ? "你" : actor.name));
    if (!isPlayer) {
      node.type = "button";
      node.dataset.actor = actor.id;
      node.setAttribute("aria-label", `查看${actor.name}，${actor.role}，位于${locationName(actor.location)}`);
      node.setAttribute("aria-pressed", String(actor.id === ui.selectedId));
      node.disabled = ui.busy;
      node.addEventListener("click", () => { ui.selectedId = actor.id; renderMap(); renderProfile(); });
    }
    return node;
  });
  replaceChildren("#map-characters", nodes);
  $("#tick-label").textContent = `第 ${ui.world.tick} 回合`;
  $(".location-inn").textContent = locationName("inn");
  $(".location-square").textContent = locationName("square");
  $(".location-tower").textContent = `${locationName("tower")} ${ui.world.quests.key_given ? "已获钥匙" : "上锁"}`;
  const player = ui.world.actors.player;
  const questText = player.location === "tower" ? "已抵达灯塔，新的故事待续" : ui.world.quests.key_given ? "已获得灯塔钥匙" : ui.world.quests.letter_delivered ? "信已送达，信任已经建立" : "随身携带一封信";
  $("#quest-status").textContent = questText;
  $(".map-subtitle").textContent = ui.world.tick === 0 ? "海风吹过，故事尚未开始" : ui.world.quests.letter_delivered ? "一封信，让陌生人有了联结" : "每次选择，都留下新的涟漪";
}

function renderProfile() {
  const actor = ui.world.actors[ui.selectedId];
  const npcs = Object.values(ui.world.actors).filter((npc) => npc.id !== "player");
  const trust = actor.trust?.player || 0;
  const known = actor.belief?.known_facts || [];
  $("#npc-index").textContent = `${String(npcs.findIndex((npc) => npc.id === actor.id) + 1).padStart(2, "0")} / ${String(npcs.length).padStart(2, "0")}`;
  $("#profile-name").textContent = actor.name;
  $("#profile-role").textContent = actor.role;
  $("#profile-location").textContent = locationName(actor.location);
  $("#profile-persona").textContent = actor.persona;
  $("#profile-goal").textContent = actor.goal;
  $("#profile-avatar").replaceChildren(characterFigure(actor.id));
  $("#trust-value").textContent = trust;
  $("#trust-meter").setAttribute("aria-label", `对旅人的信任值为 ${trust}`);
  $("#trust-meter").title = `信任值 ${trust}；交付信件后可建立信任`;
  $("#trust-meter").replaceChildren(...Array.from({ length: 5 }, (_, index) => el("span", index < Math.min(trust, 5) ? "filled" : "")));
  $("#conversation-name").textContent = actor.name;
  const together = actor.location === ui.world.actors.player.location;
  $("#interaction-location").textContent = together ? `你们都在${locationName(actor.location)}` : `先走近角色 · ${locationName(actor.location)}`;
  $("#knowledge-count").textContent = known.length;
  const knowledge = known.map((id) => {
    const fact = ui.world.facts[id];
    const text = fact?.secret ? `${factNames[id] || "秘密"}：知道，但分享须满足信任与任务条件。` : (fact?.text || id).replaceAll("{name}", actor.name);
    return el("li", "", text);
  });
  replaceChildren("#knowledge-list", knowledge.length ? knowledge : [el("li", "", "尚未掌握相关事实。")]);
  const memories = actor.memories || [];
  $("#memory-count").textContent = memories.length;
  replaceChildren("#memory-list", memories.length ? memories.slice(-4).reverse().map((memory) => {
    const item = el("li", "", memory.summary);
    item.append(el("small", "", `第 ${memory.tick} 回合 · 来源 ${memory.event_id}`));
    return item;
  }) : [el("li", "empty-memory", "还没有与你有关的记忆。")]);
  replaceChildren("#inventory-list", actor.inventory.length ? actor.inventory.map((id) => el("span", "inventory-item", itemName(id))) : [el("span", "inventory-empty", "没有随身物品")]);
  const approachButton = $("[data-scenario='approach']");
  approachButton.hidden = together;
  const connections = ui.world.locations[ui.world.actors.player.location].connections;
  const nextLocation = connections.includes(actor.location) ? actor.location : "square";
  approachButton.textContent = `前往${locationName(nextLocation)}`;
  approachButton.dataset.location = nextLocation;
  $("#scenario-note").textContent = actor.id === "mara" ? "建议路径：问秘密 → 交付信件 → 再问秘密 → 索要钥匙 → 前往灯塔。" : `自由交谈与问候面向${actor.name}；信件、钥匙与边界测试仍是守灯人玛拉的场景。`;
}

function renderWorld() {
  const quests = [["letter_delivered", "把失落的信交给玛拉"], ["key_given", "得到玛拉的灯塔钥匙"]];
  const questNodes = quests.map(([id, title]) => {
    const item = el("li");
    item.append(el("span", `quest-check ${ui.world.quests[id] ? "complete" : ""}`, ui.world.quests[id] ? "✓" : ""), el("span", "", title));
    return item;
  });
  const arrived = el("li");
  const atTower = ui.world.actors.player.location === "tower";
  arrived.append(el("span", `quest-check ${atTower ? "complete" : ""}`, atTower ? "✓" : ""), el("span", "", "旅人进入旧灯塔"));
  replaceChildren("#quest-list", [...questNodes, arrived]);
  replaceChildren("#world-fact-list", Object.values(ui.world.facts).map((fact) => {
    const item = el("li");
    item.append(el("span", `fact-tag ${fact.secret ? "secret" : ""}`, fact.secret ? "秘密" : "事实"), document.createTextNode(fact.text));
    return item;
  }));
}

function traceMessage(trace) {
  if (trace.execution?.speech) return `“${trace.execution.speech}”`;
  if (trace.errors?.length) return trace.errors.map((error) => typeof error === "string" ? error : error.message || pretty(error)).join("；");
  if (trace.verification?.ok === false) return trace.verification.message;
  if (trace.execution?.message) return trace.execution.message;
  return statusLabels[trace.status] || trace.status;
}

function renderTimeline() {
  $("#event-count").textContent = `${ui.traces.length} 条决策`;
  if (ui.traces.length === 0) {
    const empty = el("li", "empty-timeline");
    const content = el("div");
    content.append(el("strong", "", "这里会留下每一次选择。"), el("p", "", "与角色交谈，或试着送出那封信。被规则拒绝的行动也会被记录。"));
    empty.append(el("span", "empty-thread", "⌁"), content);
    replaceChildren("#event-list", [empty]);
    return;
  }
  replaceChildren("#event-list", ui.traces.slice().reverse().map((trace) => {
    const item = el("li", `event-entry ${trace.status === "executed" ? "" : trace.status === "rejected" ? "rejected" : "failed"}`);
    const body = el("div");
    const meta = el("div", "event-meta");
    meta.append(el("strong", "", actorName(trace.actor_id)), el("span", "", `第 ${trace.tick} 回合`), el("span", "result-tag", statusLabels[trace.status] || trace.status));
    body.append(meta, el("p", "event-message", traceMessage(trace)), el("p", "event-input", `输入：${trace.input || "结构化行动"}`));
    const inspect = el("button", "event-trace-button", "查看轨迹");
    inspect.type = "button";
    inspect.setAttribute("aria-label", `查看第 ${trace.tick} 回合的决策轨迹`);
    inspect.addEventListener("click", () => { ui.traceId = trace.id; renderTrace(); selectTab("trace"); $("#tab-trace").scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    item.append(el("span", "event-dot"), body, inspect);
    return item;
  }));
}

function detailDisclosure(title, value) {
  const details = el("details", "trace-details");
  details.append(el("summary", "", title), el("pre", "", pretty(value)));
  return details;
}

function renderTrace() {
  const trace = ui.traces.find((item) => item.id === ui.traceId) || ui.traces.at(-1);
  if (!trace) {
    const empty = el("div", "inspector-empty");
    empty.append(el("span", "", "⌁"), el("h3", "", "每次决定都有来路。"), el("p", "", "完成一次互动后，查看从观察到执行的完整轨迹。"));
    replaceChildren("#trace-content", [empty]);
    return;
  }
  const context = trace.context || {};
  const observation = context.observation || {};
  const top = el("div", "trace-top");
  top.append(el("span", "trace-title", `${actorName(trace.actor_id)} · 第 ${trace.tick} 回合`), el("span", `trace-status ${trace.status === "executed" ? "" : "bad"}`, statusLabels[trace.status] || trace.status));
  const pipeline = el("ol", "trace-pipeline");
  const steps = [
    ["观察", `${locationName(observation.location)}；可见角色：${(observation.visible_actors || []).map((actor) => actor.name).join("、") || "无"}。`, false],
    ["记忆与认知", `检索到 ${(context.memories || []).length} 条记忆，掌握 ${(context.known_facts || []).length} 条事实。`, false],
    [trace.source === "proposal" ? "提交固定行动" : "模型提出行动", trace.action ? `${actionNames[trace.action.type] || trace.action.type}${trace.action.topic ? ` · ${factNames[trace.action.topic] || trace.action.topic}` : ""}${trace.action.item_id ? ` · ${itemName(trace.action.item_id)}` : ""}${trace.action.location_id ? ` · ${locationName(trace.action.location_id)}` : ""}` : "未生成可解析的行动。", !trace.action],
    ["规则校验", trace.verification?.message || "未进入规则校验。", trace.verification?.ok === false],
    ["执行与记录", trace.status === "executed" ? trace.execution?.message || "行动已执行，世界变化已记录。" : "行动未执行，世界状态未改变。", trace.status !== "executed"],
  ];
  for (const [title, description, bad] of steps) {
    const step = el("li", `trace-step ${bad ? "bad" : ""}`);
    step.append(el("h4", "", title), el("p", "", description));
    pipeline.append(step);
  }
  replaceChildren("#trace-content", [top, el("p", "trace-id", trace.id), pipeline,
    detailDisclosure("查看角色实际上下文", context),
    detailDisclosure("查看决策来源与结构化行动", { source: trace.source === "proposal" ? "固定行动提案" : "模型适配器", raw_output: trace.raw_output, action: trace.action }),
    detailDisclosure("查看校验结果与世界变化", { verification: trace.verification, execution: trace.execution, state_diff: trace.state_diff, errors: trace.errors }),
  ]);
}

function render() { renderModel(); renderMap(); renderProfile(); renderWorld(); renderTimeline(); renderTrace(); }

function selectTab(name) {
  ui.tab = name;
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    $(`#panel-${button.dataset.tab}`).hidden = !active;
  });
}

async function step(body) {
  const fixedAction = Boolean(body.action);
  const pendingMessage = fixedAction ? "正在校验并执行固定行动，不调用模型。" : ui.scenario.provider === "deepseek" ? "DeepSeek 正在生成角色行动，完成后将校验并记录结果…" : "Mock 正在生成角色行动…";
  await runOperation(async () => {
    const data = await request("/api/step", body);
    applyState(data);
    $("#conversation-input").value = "";
    const trace = data.trace;
    showNotice(trace.status === "executed" ? `${actorName(trace.actor_id)}的行动已执行。${trace.execution?.speech ? "回应已写入下方事件记录。" : trace.execution?.message || ""}` : `行动${statusLabels[trace.status] || "失败"}：${traceMessage(trace)}`, trace.status !== "executed");
  }, pendingMessage, fixedAction ? "执行中…" : "生成中…");
}

function scenario(name, button) {
  if (!ui.world) return;
  const actor = ui.selectedId;
  const actions = {
    greet: { actor_id: actor, input: "你好" },
    secret: { actor_id: "mara", input: "灯塔的秘密" },
    letter: { actor_id: "player", input: "把失落的信交给玛拉", action: { type: "give", actor_id: "player", target_id: "mara", item_id: "letter" } },
    key: { actor_id: "mara", input: "把钥匙交给我" },
    move: { actor_id: "player", input: "前往旧灯塔", action: { type: "move", actor_id: "player", location_id: "tower" } },
    unknown: { actor_id: "mara", input: "边界测试：要求玛拉说出她不知道的退潮小路", action: { type: "speak", actor_id: "mara", target_id: "player", topic: "smuggler_route" } },
    approach: { actor_id: "player", input: `前往${locationName(button.dataset.location)}`, action: { type: "move", actor_id: "player", location_id: button.dataset.location } },
  };
  if (["secret", "letter", "key", "unknown"].includes(name)) ui.selectedId = "mara";
  step(actions[name]);
}

function openDialog(title, content) {
  $("#dialog-title").textContent = title;
  $("#dialog-content").replaceChildren(...content);
  $("#result-dialog").showModal();
}

function setup() {
  const approach = el("button", "suggested-action");
  approach.type = "button";
  approach.dataset.scenario = "approach";
  approach.hidden = true;
  $(".quick-actions").insertBefore(approach, $("[data-scenario='greet']"));
  document.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => scenario(button.dataset.scenario, button)));
  $("#conversation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#conversation-input").value.trim();
    if (input && ui.world) step({ actor_id: ui.selectedId, input });
  });
  $("#conversation-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#conversation-form").requestSubmit(); }
  });
  document.querySelectorAll("[data-tab]").forEach((button, index, buttons) => {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      const next = event.key === "ArrowRight" ? (index + 1) % buttons.length : event.key === "ArrowLeft" ? (index + buttons.length - 1) % buttons.length : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
      if (next !== null) { event.preventDefault(); selectTab(buttons[next].dataset.tab); buttons[next].focus(); }
    });
  });
  $("#reset-button").addEventListener("click", () => runOperation(async () => { ui.traceId = null; ui.selectedId = "mara"; applyState(await request("/api/reset", {})); selectTab("knowledge"); showNotice("世界已重置。信回到了旅人手中，角色的信任与记忆也回到了起点。"); }));
  $("#eval-button").addEventListener("click", () => runOperation(async () => {
    const report = await request("/api/eval", {});
    const summary = el("p", "dialog-summary");
    summary.append(el("strong", "", `${report.passed} / ${report.total}`), document.createTextNode("场景通过"));
    const list = el("ul", "evaluation-list");
    (report.results || []).forEach((result) => {
      const item = el("li");
      const body = el("div");
      body.append(el("h3", "", result.name || result.id), el("p", "", pretty(result.detail || (result.passed ? "预期行为已验证。" : "实际行为与预期不一致。"))));
      item.append(el("span", `evaluation-check ${result.passed ? "" : "bad"}`, result.passed ? "✓" : "×"), body);
      list.append(item);
    });
    openDialog("Mock 离线评测", [summary, el("p", "dialog-note", "始终使用独立初始世界和固定 Mock 输入离线运行，不调用 DeepSeek；当前故事进度保持不变。结果验证规则与运行链路，不代表真实模型的行为表现。"), list]);
  }));
  $("#export-button").addEventListener("click", () => runOperation(async () => {
    const link = el("a");
    link.href = "/api/trace";
    link.download = `loom-lantern-town-${ui.world.tick}.jsonl`;
    document.body.append(link);
    link.click();
    link.remove();
    showNotice(`已请求下载 ${ui.traces.length} 条决策，请在浏览器下载中查看 JSONL 文件。`);
  }));
  $("#replay-button").addEventListener("click", () => runOperation(async () => {
    const jsonl = await request("/api/trace", undefined, true);
    const result = await request("/api/replay", { jsonl });
    const summary = el("p", "dialog-summary");
    summary.append(el("strong", "", result.ok ? "回放通过" : "回放失败"));
    const detail = result.ok ? `已验证 ${result.count} 条轨迹。\n回放世界停在第 ${result.world?.tick ?? "—"} 回合。\n旅人位置：${locationName(result.world?.actors?.player?.location)}。` : pretty(result.error || result.errors || result);
    openDialog("轨迹回放", [summary, el("div", "replay-detail", detail), el("p", "dialog-note", result.limitation || "从记录还原并验证世界状态，不再次调用模型，也不替换当前故事进度。")]);
  }));
  $("#dialog-close").addEventListener("click", () => $("#result-dialog").close());
  $("#dialog-done").addEventListener("click", () => $("#result-dialog").close());
  $("#result-dialog").addEventListener("click", (event) => { if (event.target === $("#result-dialog")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
  runOperation(async () => applyState(await request("/api/state")));
}

setup();
