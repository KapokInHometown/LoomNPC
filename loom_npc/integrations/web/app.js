"use strict";

// The browser is a debugger and a player interface. Only the server builds NPC context.
const $ = (selector) => document.querySelector(selector);
const ui = { world: null, traces: [], scenario: null, selectedId: null, traceId: null, traceFilename: null, busy: false, tab: "knowledge", art: null };
const defaultStyle = { coat: "#648a84", light: "#adc3aa", hair: "#645954", skin: "#e7c9a2" };
const presentation = () => ui.scenario.presentation;
const statusLabels = { executed: "已执行", rejected: "规则拒绝", parse_error: "解析失败", model_error: "模型失败", execution_error: "执行失败" };
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
function factName(id) { return presentation().fact_labels?.[id] || ui.world.facts[id]?.aliases?.[0] || id; }
function pretty(value) { return typeof value === "string" ? value : JSON.stringify(value, null, 2); }

function normalizeState(data) {
  if (!data.world || !data.world.actors || !Array.isArray(data.traces)) throw new Error("世界数据不完整，请重置后重试。");
  if (!data.scenario || !["mock", "deepseek"].includes(data.scenario.provider)) throw new Error("运行模型信息不完整，请刷新页面重试。");
  if (!data.scenario.presentation || !Object.hasOwn(data.world.actors, data.scenario.presentation.default_actor)) throw new Error("页面展示配置不完整，请刷新页面重试。");
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
  document.querySelectorAll("#conversation-form button, #reset-button, #eval-button, #actor-select, [data-scenario], [data-actor]").forEach((button) => { button.disabled = value; });
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
  ui.traceFilename = data.trace_filename;
  if (ui.selectedId === null || !Object.hasOwn(ui.world.actors, ui.selectedId)) ui.selectedId = presentation().default_actor;
  if (data.trace) ui.traceId = data.trace.id;
  else if (!ui.traces.some((trace) => trace.id === ui.traceId)) ui.traceId = ui.traces.at(-1)?.id || null;
  render();
}

function renderModel() {
  const online = ui.scenario.provider === "deepseek";
  const indicator = $(".model-indicator");
  indicator.dataset.provider = ui.scenario.provider;
  indicator.replaceChildren(el("span"), document.createTextNode(online ? "DeepSeek · 在线" : "Mock · 离线运行"));
  indicator.title = `当前模型：${ui.scenario.model}`;
  $("#runtime-footer").textContent = online ? `本地运行时 · DeepSeek 在线 · ${ui.scenario.model}` : "本地原型 · 确定性 Mock · 无需 API Key";
  $("#model-note").textContent = online
    ? "文本输入与文字建议由 DeepSeek 决策。输入与角色可见上下文会发至 DeepSeek，密钥仅由本地服务持有。固定行动仅由世界规则校验。"
    : "文本输入与文字建议由本地 Mock 决策，固定行动由世界规则校验，均不调用外部模型。";
  if (ui.scenario.speech_enabled) $("#model-note").textContent = (online
    ? "行动由 DeepSeek 决策，输入与角色上下文会发送给模型；固定提案也经过世界规则校验。"
    : "行动由本地 Mock 决策，固定提案也经过世界规则校验。")
    + "已开启非秘密台词生成：回复仅供展示，语义未校验，失败时保留固定回应。";
}

function characterFigure(id) {
  const p = presentation().actor_styles?.[id] || defaultStyle;
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
  if (p.detail === "long-hair") shape("path", { d: "m9 8-1 8 4 2-1-8Z", fill: p.hair });
  if (p.detail === "hat") shape("path", { d: "m7 6 4-5 11 1 3 6Z", fill: "#9d8e69" });
  if (p.detail === "helmet") shape("path", { d: "M8 7Q16-6 24 7l1 3H7Z", fill: "#748b94" });
  if (p.detail === "scarf") shape("path", { d: "m7 18 18 1-5 7-9-2Z", fill: "#e7d18b" });
  shape("circle", { cx: 18, cy: 10, r: .8, fill: "#5f6158" });
  return svg;
}

// These predicates describe presentation only; all actions still use the server verifier.
function matches(when = {}) {
  return (when.quest_id === undefined || ui.world.quests[when.quest_id] === true)
    && (when.actor_id === undefined || ui.world.actors[when.actor_id]?.location === when.location_id)
    && (when.tick === undefined || ui.world.tick === when.tick);
}

function narrative(entries, fallback) { return entries?.find((entry) => matches(entry.when))?.text || fallback; }

function selectActor(id) {
  ui.selectedId = id;
  renderMap();
  renderProfile();
}

function actorButton(actor, className) {
  const node = el("button", `${className} ${actor.id === ui.selectedId ? "selected" : ""}`);
  node.type = "button";
  node.dataset.actor = actor.id;
  node.setAttribute("aria-label", `查看${actor.name}，${actor.role}，位于${locationName(actor.location)}`);
  node.setAttribute("aria-pressed", String(actor.id === ui.selectedId));
  node.disabled = ui.busy;
  node.append(characterFigure(actor.id), el("span", "character-label", actor.name));
  node.addEventListener("click", () => selectActor(actor.id));
  return node;
}

function renderMap() {
  const view = presentation();
  const illustrated = view.art === "lantern-town";
  $("#world-stage").classList.toggle("generic-world", !illustrated);
  $("#world-stage").setAttribute("aria-label", `${ui.world.name}世界地图`);
  document.title = `Loom NPC 织幕 · ${ui.world.name}`;
  $("#world-heading").textContent = `${ui.world.name} · 可交互原型`;
  $(".intro-description").textContent = ui.scenario.description;
  $(".map-title").textContent = ui.world.name;
  $(".map-subtitle").textContent = narrative(view.subtitle, "地点连接与角色位置");
  $("#tick-label").textContent = `第 ${ui.world.tick} 回合`;
  if (ui.art !== view.art) {
    $("#map-art").replaceChildren(...(illustrated ? [$("#town-art").content.cloneNode(true)] : []));
    ui.art = view.art;
  }
  $("#generic-map").hidden = illustrated;
  $("#map-characters").hidden = !illustrated;
  $("#map-locations").hidden = !illustrated;
  const actors = Object.values(ui.world.actors);
  if (illustrated) {
    replaceChildren("#map-locations", Object.values(ui.world.locations).map((place) => {
      const label = el("div", `location-label location-${place.id}`, `${place.name} ${narrative(view.location_status?.[place.id], "")}`.trim());
      const [x, y] = view.location_labels[place.id];
      label.style.left = `${x}%`;
      label.style.top = `${y}%`;
      return label;
    }));
    replaceChildren("#map-characters", actors.map((actor) => {
      const isViewpoint = actor.id === view.viewpoint_actor;
      const node = actorButton(actor, `map-character ${isViewpoint ? "viewpoint" : ""}`);
      const [x, y] = view.location_points[actor.location];
      const peers = actors.filter((other) => other.location === actor.location && other.id !== view.viewpoint_actor);
      const offset = isViewpoint ? [7, 3] : [peers.findIndex((other) => other.id === actor.id) * 5, 0];
      node.style.left = `${x + offset[0]}%`;
      node.style.top = `${y + offset[1]}%`;
      return node;
    }));
  } else {
    replaceChildren("#generic-map", Object.values(ui.world.locations).map((place) => {
      const card = el("section", "location-card");
      card.dataset.location = place.id;
      card.append(el("h3", "", place.name), el("p", "location-description", place.description),
        el("p", "location-connections", place.connections.length ? `通往：${place.connections.map(locationName).join("、")}` : "没有通往其他地点的路径"));
      const residents = el("div", "location-actors");
      const present = actors.filter((actor) => actor.location === place.id);
      residents.append(...(present.length ? present.map((actor) => actorButton(actor, "location-actor")) : [el("span", "empty-location", "暂无角色")]));
      card.append(residents);
      return card;
    }));
  }
  const quests = Object.values(ui.world.quests);
  $("#quest-status").textContent = narrative(view.progress, quests.length ? `任务状态：${quests.filter(Boolean).length} / ${quests.length} 已达成` : "当前世界没有任务标志");
  const legend = $("#viewpoint-legend");
  legend.hidden = !view.viewpoint_actor;
  legend.textContent = view.viewpoint_actor ? `你 · ${actorName(view.viewpoint_actor)}` : "";
}

function shortcutButton(shortcut) {
  const button = el("button", shortcut.class || "", shortcut.label);
  button.type = "button";
  button.dataset.scenario = shortcut.id;
  button.disabled = ui.busy;
  button.title = shortcut.body.action ? (ui.scenario.speech_enabled && shortcut.body.action.type === "speak"
    ? "固定行动经规则校验；合法非秘密话题可生成台词" : "直接提交固定行动，由世界规则校验，不调用模型") : "使用当前模型生成行动，再交由世界规则校验";
  button.addEventListener("click", () => {
    if (shortcut.select_actor) selectActor(shortcut.select_actor);
    step({ ...shortcut.body, actor_id: shortcut.body.actor_id === "$selected" ? ui.selectedId : shortcut.body.actor_id });
  });
  return button;
}

function renderInteractions(actor) {
  const view = presentation();
  const choices = [];
  const viewpoint = view.viewpoint_actor ? ui.world.actors[view.viewpoint_actor] : null;
  if (viewpoint && viewpoint.id !== actor.id && viewpoint.location !== actor.location) {
    const destination = ui.world.locations[viewpoint.location].connections.find((id) => id === actor.location)
      || ui.world.locations[viewpoint.location].connections[0];
    if (destination && viewpoint.allowed_actions.includes("move")) choices.push({ id: "approach", label: `前往${locationName(destination)}`, class: "suggested-action", body: {
      actor_id: viewpoint.id, input: `前往${locationName(destination)}`, action: { type: "move", actor_id: viewpoint.id, location_id: destination },
    } });
  }
  if (view.shortcuts) choices.push(...view.shortcuts);
  else {
    const peers = Object.values(ui.world.actors).filter((other) => other.id !== actor.id && other.location === actor.location);
    const add = (id, input) => choices.push({ id, label: input, body: { actor_id: actor.id, input } });
    if (actor.allowed_actions.includes("give")) for (const item of actor.inventory) for (const peer of peers) {
      add(`give-${item}-${peer.id}`, `把${itemName(item)}交给${peer.name}`);
    }
    if (actor.allowed_actions.includes("speak")) for (const topic of actor.belief.known_facts) for (const peer of peers) {
      add(`speak-${topic}-${peer.id}`, `向${peer.name}说明${factName(topic)}`);
    }
    if (actor.allowed_actions.includes("move")) for (const destination of ui.world.locations[actor.location].connections) {
      if (destination !== actor.location) add(`move-${destination}`, `进入${locationName(destination)}`);
    }
  }
  replaceChildren(".quick-actions", choices.length ? [el("span", "quick-label", "试一试"), ...choices.map(shortcutButton)] : []);
  $("#scenario-note").textContent = view.interaction_note || `可用行动：${actor.allowed_actions.map((id) => actionNames[id] || id).join("、") || "无"}。文字建议只表达意图，任务条件由世界规则校验。`;
}

function renderProfile() {
  const actor = ui.world.actors[ui.selectedId];
  const actors = Object.values(ui.world.actors);
  const known = actor.belief.known_facts;
  const options = actors.map((other) => {
    const option = el("option", "", `${other.name} · ${locationName(other.location)}`);
    option.value = other.id;
    return option;
  });
  replaceChildren("#actor-select", options);
  $("#actor-select").value = actor.id;
  $("#npc-index").textContent = `${String(actors.findIndex((other) => other.id === actor.id) + 1).padStart(2, "0")} / ${String(actors.length).padStart(2, "0")}`;
  $("#profile-name").textContent = actor.name;
  $("#profile-role").textContent = actor.role;
  $("#profile-location").textContent = locationName(actor.location);
  $("#profile-persona").textContent = actor.persona;
  $("#profile-goal").textContent = actor.goal;
  $("#profile-avatar").replaceChildren(characterFigure(actor.id));
  const trust = Object.entries(actor.trust).map(([target, value]) => {
    const row = el("div", "trust-row");
    const meter = el("div", "trust-meter");
    meter.setAttribute("aria-label", `对${actorName(target)}的信任值为 ${value}`);
    meter.append(...Array.from({ length: 5 }, (_, index) => el("span", index < Math.min(value, 5) ? "filled" : "")));
    row.append(el("span", "", `对${actorName(target)}的信任`), meter, el("strong", "", value));
    return row;
  });
  replaceChildren("#trust-relationships", trust.length ? trust : [el("p", "relationship-empty", "尚未建立信任关系")]);
  $("#conversation-name").textContent = actor.name;
  const viewpointId = presentation().viewpoint_actor;
  const viewpoint = viewpointId ? ui.world.actors[viewpointId] : null;
  const dialogue = viewpoint && actor.id !== viewpoint.id;
  $("#interaction-verb").textContent = dialogue ? "与" : "让";
  $("#interaction-suffix").textContent = dialogue ? "交谈" : "行动";
  $("#interaction-location").textContent = dialogue ? (actor.location === viewpoint.location ? `你们都在${locationName(actor.location)}` : `先走近角色 · ${locationName(actor.location)}`) : `当前位于${locationName(actor.location)}`;
  $("#conversation-input").placeholder = `为${actor.name}输入行动或对话……`;
  $("#knowledge-count").textContent = known.length;
  const knowledge = known.map((id) => {
    const fact = ui.world.facts[id];
    const text = fact.secret ? `${factName(id)}：知道，但分享须满足信任与任务条件。` : fact.text.replaceAll("{name}", actor.name);
    return el("li", "", text);
  });
  replaceChildren("#knowledge-list", knowledge.length ? knowledge : [el("li", "", "尚未掌握相关事实。")]);
  const memories = actor.memories;
  $("#memory-count").textContent = memories.length;
  replaceChildren("#memory-list", memories.length ? memories.slice(-4).reverse().map((memory) => {
    const item = el("li", "", memory.summary);
    item.append(el("small", "", `第 ${memory.tick} 回合 · 来源 ${memory.event_id}`));
    return item;
  }) : [el("li", "empty-memory", "还没有相关记忆。")]);
  replaceChildren("#inventory-list", actor.inventory.length ? actor.inventory.map((id) => el("span", "inventory-item", itemName(id))) : [el("span", "inventory-empty", "没有随身物品")]);
  renderInteractions(actor);
}

function questLabel(id) {
  if (presentation().quest_labels?.[id]) return presentation().quest_labels[id];
  const rule = (ui.world.rules || []).find((rule) => rule.effects.some((effect) => effect.type === "set_quest" && effect.quest_id === id && effect.value));
  if (rule?.summary_suffix) return rule.summary_suffix;
  if (rule?.match.topic) return `说明${factName(rule.match.topic)}`;
  return id;
}

function renderWorld() {
  const quests = Object.entries(ui.world.quests).map(([id, complete]) => ({ label: questLabel(id), complete, id }));
  const objectives = (presentation().objectives || []).map((goal) => ({ label: goal.label, complete: matches(goal.when) }));
  const nodes = [...quests, ...objectives].map(({ label, complete, id }) => {
    const item = el("li");
    if (id) { item.dataset.quest = id; item.title = id; }
    item.append(el("span", `quest-check ${complete ? "complete" : ""}`, complete ? "✓" : ""), el("span", "", label));
    return item;
  });
  replaceChildren("#quest-list", nodes.length ? nodes : [el("li", "", "当前世界没有任务标志。")]);
  replaceChildren("#world-fact-list", Object.values(ui.world.facts).map((fact) => {
    const item = el("li");
    item.append(el("span", `fact-tag ${fact.secret ? "secret" : ""}`, fact.secret ? "秘密" : "事实"), document.createTextNode(fact.text));
    return item;
  }));
}

function traceMessage(trace) {
  if (trace.speech?.status === "generated") return `“${trace.speech.text}”（生成台词，语义未校验）`;
  if (trace.speech?.status === "fallback") return `“${trace.speech.text}”（生成失败，使用固定回应）`;
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
    content.append(el("strong", "", "这里会留下每一次选择。"), el("p", "", "输入角色行动。被规则拒绝的行动也会被记录。"));
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
    [trace.source === "proposal" ? "提交固定行动" : "模型提出行动", trace.action ? `${actionNames[trace.action.type] || trace.action.type}${trace.action.topic ? ` · ${factName(trace.action.topic)}` : ""}${trace.action.item_id ? ` · ${itemName(trace.action.item_id)}` : ""}${trace.action.location_id ? ` · ${locationName(trace.action.location_id)}` : ""}` : "未生成可解析的行动。", !trace.action],
    ["规则校验", trace.verification?.message || "未进入规则校验。", trace.verification?.ok === false],
    ["执行与记录", trace.status === "executed" ? trace.execution?.message || "行动已执行，世界变化已记录。" : "行动未执行，世界状态未改变。", trace.status !== "executed"],
  ];
  if (trace.speech) steps.push(["角色回应", trace.speech.status === "generated" ? "生成台词仅供展示，未经过语义安全校验。"
    : trace.speech.status === "skipped" ? "秘密话题使用固定回应。" : "台词生成失败或格式无效，使用固定回应。", false]);
  for (const [title, description, bad] of steps) {
    const step = el("li", `trace-step ${bad ? "bad" : ""}`);
    step.append(el("h4", "", title), el("p", "", description));
    pipeline.append(step);
  }
  replaceChildren("#trace-content", [top, el("p", "trace-id", trace.id), pipeline,
    detailDisclosure("查看角色实际上下文", context),
    detailDisclosure("查看决策来源与结构化行动", { source: trace.source === "proposal" ? "固定行动提案" : "模型适配器", raw_output: trace.raw_output, action: trace.action }),
    detailDisclosure("查看校验结果与世界变化", { verification: trace.verification, execution: trace.execution, state_diff: trace.state_diff, errors: trace.errors }),
    ...(trace.speech ? [detailDisclosure("查看台词生成与回退", trace.speech)] : []),
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
  const pendingMessage = fixedAction ? (ui.scenario.speech_enabled && body.action.type === "speak"
    ? "正在校验固定行动，合法非秘密话题可生成回应…" : "正在校验并执行固定行动，不调用模型。") : ui.scenario.provider === "deepseek" ? "DeepSeek 正在生成角色行动，完成后将校验并记录结果…" : "Mock 正在生成角色行动…";
  await runOperation(async () => {
    const data = await request("/api/step", body);
    applyState(data);
    $("#conversation-input").value = "";
    const trace = data.trace;
    showNotice(trace.status === "executed" ? `${actorName(trace.actor_id)}的行动已执行。${trace.execution?.speech ? "回应已写入下方事件记录。" : trace.execution?.message || ""}` : `行动${statusLabels[trace.status] || "失败"}：${traceMessage(trace)}`, trace.status !== "executed");
  }, pendingMessage, fixedAction ? "执行中…" : "生成中…");
}

function openDialog(title, content) {
  $("#dialog-title").textContent = title;
  $("#dialog-content").replaceChildren(...content);
  $("#result-dialog").showModal();
}

function setup() {
  $("#actor-select").addEventListener("change", (event) => selectActor(event.target.value));
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
  $("#reset-button").addEventListener("click", () => runOperation(async () => { ui.traceId = null; ui.selectedId = null; applyState(await request("/api/reset", {})); selectTab("knowledge"); showNotice("世界已重置。角色位置、物品、信任、任务与记忆回到了起点。"); }));
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
    openDialog("Mock 离线评测", [summary, el("p", "dialog-note", "始终使用随包的独立灯港镇世界和固定 Mock 输入离线运行，不调用 DeepSeek；当前故事进度保持不变。结果验证规则与运行链路，不代表真实模型的行为表现。"), list]);
  }));
  $("#export-button").addEventListener("click", () => runOperation(async () => {
    const link = el("a");
    link.href = "/api/trace";
    link.download = ui.traceFilename;
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
    const detail = result.ok ? `已验证 ${result.count} 条轨迹。\n回放世界停在第 ${result.world?.tick ?? "—"} 回合。\n角色位置：${Object.values(result.world.actors).map((actor) => `${actor.name}在${locationName(actor.location)}`).join("、")}。` : pretty(result.error || result.errors || result);
    openDialog("轨迹回放", [summary, el("div", "replay-detail", detail), el("p", "dialog-note", result.limitation || "从记录还原并验证世界状态，不再次调用模型，也不替换当前故事进度。")]);
  }));
  $("#dialog-close").addEventListener("click", () => $("#result-dialog").close());
  $("#dialog-done").addEventListener("click", () => $("#result-dialog").close());
  $("#result-dialog").addEventListener("click", (event) => { if (event.target === $("#result-dialog")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
  runOperation(async () => applyState(await request("/api/state")));
}

setup();
