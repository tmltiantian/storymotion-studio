import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the pet drama screening room", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>宠物短剧项目放映室<\/title>/i);
  assert.match(html, /咪要去面试/);
  assert.match(html, /宠物短剧项目放映室/);
  assert.match(html, /\/media\/final-master\.mp4/);
  assert.match(html, /成片/);
  assert.match(html, /问题修复/);
  assert.match(html, /迭代记录/);
  assert.match(html, /三种输入，共用一条可验收生产线/);
  assert.match(html, /原创短剧/);
  assert.match(html, /小说漫剧/);
  assert.match(html, /参考复刻/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|Building your site/);
});

test("keeps project evidence, accessibility, and responsive rules in the shipped source", async () => {
  const [page, css, layout, packageJson, video, actionAudit, transitions] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    stat(new URL("../public/media/final-master.mp4", import.meta.url)),
    stat(new URL("../public/evidence/V3_action_frame_audit.jpg", import.meta.url)),
    stat(new URL("../public/evidence/V3_eight_transitions.jpg", import.meta.url)),
  ]);

  assert.ok(video.size > 0);
  assert.ok(actionAudit.size > 0);
  assert.ok(transitions.size > 0);
  assert.match(page, /role="tablist"/);
  assert.match(page, /aria-selected=\{selected\}/);
  assert.match(page, /onKeyDown/);
  assert.match(page, /861/);
  assert.match(page, /229/);
  assert.match(page, /2847/);
  assert.match(page, /62/);
  assert.match(page, /构思/);
  assert.match(page, /剧本/);
  assert.match(page, /分镜/);
  assert.match(page, /素材/);
  assert.match(page, /音频/);
  assert.match(page, /视频/);
  assert.match(page, /剪辑/);
  assert.match(page, /EVAL/);
  assert.match(page, /交付/);
  assert.match(css, /\.mode-strip/);
  assert.match(css, /@media \(max-width: 720px\)/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient/);
  assert.match(layout, /lang="zh-CN"/);
  assert.match(layout, /宠物短剧项目放映室/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});

test("ships a categorized history library with exclusive media playback", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  for (const filename of [
    "old-city-letter.mp4",
    "six-voices.mp4",
    "three-role-dialogue.mp4",
    "freeze-dried-v1.mp4",
    "freeze-dried-refined.mp4",
    "cat-ideas-pilot.mp4",
    "cat-ideas-final.mp4",
    "interview-cat-v2.mp4",
  ]) {
    assert.match(page, new RegExp(filename.replace(".", "\\.")));
  }

  assert.match(page, /type ArchiveCategory = "all" \| "comic" \| "voice" \| "pet"/);
  assert.match(page, /onPlayMedia/);
  assert.match(page, /querySelectorAll<HTMLMediaElement>\("audio, video"\)/);
  assert.match(page, /media !== current/);
  assert.match(page, /media\.pause\(\)/);
  assert.match(css, /\.archive-grid/);
  assert.match(css, /\.archive-card/);
  assert.match(css, /@media \(max-width: 720px\)/);
});

test("ships approved cat voices, playable clips, and the final asset summary", async () => {
  const [page, css, blackVoice, orangeVoice, dialogue] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    stat(new URL("../public/audio/black-cat-approved.m4a", import.meta.url)),
    stat(new URL("../public/audio/orange-cat-approved.m4a", import.meta.url)),
    stat(new URL("../public/audio/two-cat-approved-dialogue.m4a", import.meta.url)),
  ]);

  assert.ok(blackVoice.size > 0);
  assert.ok(orangeVoice.size > 0);
  assert.ok(dialogue.size > 0);
  assert.match(page, /角色声音/);
  assert.match(page, /魅力女友/);
  assert.match(page, /调皮公主/);
  assert.match(page, /rate: "\+4"/);
  assert.match(page, /rate: "\+2"/);
  assert.match(page, /语速 \{item\.rate\}/);
  assert.match(page, /black-cat-approved\.m4a/);
  assert.match(page, /orange-cat-approved\.m4a/);
  assert.match(page, /two-cat-approved-dialogue\.m4a/);
  assert.match(page, /已确认资产/);
  assert.match(page, /Seed-TTS 2\.0/);
  assert.match(page, /Seedance 2\.0/);
  assert.match(page, /querySelectorAll<HTMLMediaElement>\("audio, video"\)/);
  assert.match(css, /\.voice-grid/);
  assert.match(css, /\.voice-player/);
  assert.match(css, /\.asset-register/);
});
