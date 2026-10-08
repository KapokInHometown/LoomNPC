/** Browser regression cases. Accept a Playwright Page or a Codex tab.playwright. */
import assert from "node:assert/strict";

async function count(page, expected) {
  await page.locator("#event-count").getByText(`${expected} 条决策`, { exact: true }).waitFor({ state: "visible" });
}

async function submit(page, actor, input, expectedCount) {
  await page.getByRole("combobox", { name: "行动角色" }).selectOption(actor);
  await page.getByRole("textbox", { name: "角色行动或对话" }).fill(input);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await count(page, expectedCount);
}

async function reset(page) {
  await page.getByRole("button", { name: "重置世界", exact: true }).click();
  await count(page, 0);
  assert.equal(await page.locator("#tick-label").innerText(), "第 0 回合");
}

async function replay(page, expected) {
  await page.getByRole("button", { name: "回放", exact: true }).click();
  await page.getByRole("heading", { name: "轨迹回放", exact: true }).waitFor({ state: "visible" });
  assert.match(await page.locator("#dialog-content").innerText(), /回放通过/);
  assert.match(await page.locator("#dialog-content").innerText(), expected);
  await page.getByRole("button", { name: "完成", exact: true }).click();
}

async function trace(page, expectedSource) {
  await page.getByRole("tab", { name: "决策轨迹", exact: true }).click();
  await page.getByText("查看决策来源与结构化行动", { exact: true }).click();
  assert.match(await page.locator("#trace-content").innerText(), expectedSource);
  // The knowledge panel still renders separately; tab switching must preserve selection.
  await page.getByRole("tab", { name: "世界真相", exact: true }).click();
}

export async function verifyWorkshop(page) {
  await reset(page);
  assert.equal(await page.locator(".map-title").innerText(), "山间工坊");
  assert.equal(await page.locator(".town-map").count(), 0);
  assert.equal(await page.locator("#actor-select option").count(), 2);
  assert.equal(await page.getByRole("button", { name: "询问灯塔秘密", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "打个招呼", exact: true }).count(), 0);
  assert.match(await page.locator("#generic-map").innerText(), /通往：锻造间/);
  await submit(page, "apprentice", "进入锻造间", 1);
  assert.match(await page.locator("#notice").innerText(), /规则拒绝.*先听取/);
  assert.equal(await page.locator("#tick-label").innerText(), "第 0 回合");
  assert.match(await page.locator("#quest-status").innerText(), /0 \/ 3/);
  await submit(page, "smith", "请向学徒说明开工计划", 2);
  assert.match(await page.locator("#notice").innerText(), /规则拒绝.*秘密不能透露/);
  assert.equal(await page.locator("#tick-label").innerText(), "第 0 回合");
  await submit(page, "apprentice", "把矿石交给工匠", 3);
  assert.match(await page.locator("#notice").innerText(), /已执行/);
  assert.equal(await page.locator("#inventory-list").innerText(), "没有随身物品");
  await page.getByRole("combobox", { name: "行动角色" }).selectOption("smith");
  assert.match(await page.locator("#trust-relationships").innerText(), /对学徒的信任\s*2/);
  // Generated suggestions are ordinary language, not workshop-specific fixed actions.
  await page.getByRole("button", { name: "向学徒说明开工计划", exact: true }).click();
  await count(page, 4);
  await submit(page, "apprentice", "进入锻造间", 5);
  assert.match(await page.locator("#quest-status").innerText(), /3 \/ 3/);
  assert.equal(await page.locator("#profile-location").innerText(), "锻造间");
  assert.match(await page.locator('[data-location="forge"]').innerText(), /学徒/);
  assert.doesNotMatch(await page.locator('[data-location="yard"]').innerText(), /学徒\s*工匠/);
  await trace(page, /模型适配器/);
  assert.equal(await page.locator("#quest-list .complete").count(), 3);
  await replay(page, /学徒在锻造间、工匠在前院/);
  assert.equal(await page.locator("#event-list .rejected").count(), 2);
  assert.equal(await page.locator("#event-list .failed").count(), 0);
}

export async function verifyTown(page) {
  await reset(page);
  assert.equal(await page.locator(".map-title").innerText(), "灯港镇");
  assert.equal(await page.locator(".town-map").count(), 1);
  assert.equal(await page.locator("#actor-select option").count(), 4);
  for (const [label, expected] of [["打个招呼", 1], ["询问灯塔秘密", 2], ["测试知识边界", 3], ["交付信件", 4], ["询问灯塔秘密", 5], ["索要钥匙", 6], ["前往灯塔", 7]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await count(page, expected);
    if (expected === 2 || expected === 3) assert.match(await page.locator("#notice").innerText(), /规则拒绝/);
    else assert.match(await page.locator("#notice").innerText(), /已执行/);
  }
  assert.equal(await page.locator("#tick-label").innerText(), "第 5 回合");
  assert.match(await page.locator("#quest-status").innerText(), /已抵达灯塔/);
  await trace(page, /固定行动提案/);
  assert.equal(await page.locator("#quest-list .complete").count(), 3);
  await replay(page, /旅人在旧灯塔/);
  assert.equal(await page.locator("#event-list .rejected").count(), 2);
  assert.equal(await page.locator("#event-list .failed").count(), 0);
  // Walk to Ivo using the scene's approach button after a full quest.
  await page.getByRole("combobox", { name: "行动角色" }).selectOption("ivo");
  await page.getByRole("button", { name: "前往潮汐广场", exact: true }).click();
  await count(page, 8);
  await page.getByRole("button", { name: "前往渡鸥旅店", exact: true }).click();
  await count(page, 9);
  await page.getByRole("button", { name: "打个招呼", exact: true }).click();
  await count(page, 10);
  assert.match(await page.locator("#notice").innerText(), /伊沃的行动已执行/);
}

export async function verifyMinimal(page) {
  await reset(page);
  assert.equal(await page.locator("#actor-select option").count(), 1);
  assert.equal(await page.locator(".town-map").count(), 0);
  assert.equal(await page.locator("[data-scenario]").count(), 0);
  assert.match(await page.locator("#quest-status").innerText(), /没有任务标志/);
  assert.equal(await page.locator("#send-button").isEnabled(), true);
  await page.getByRole("tab", { name: "世界真相", exact: true }).click();
  assert.match(await page.locator("#quest-list").innerText(), /没有任务标志/);
  await page.getByRole("tab", { name: "角色认知", exact: true }).click();
  assert.match(await page.locator("#knowledge-list").innerText(), /尚未掌握/);
  assert.match(await page.locator("#inventory-list").innerText(), /没有随身物品/);
}
