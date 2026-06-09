// wxextract — Instagram DM console dumper (browser-agnostic).
// 1. Open the DM at instagram.com/direct/t/<id>/ while logged in.
// 2. Open DevTools → Console, paste this, press Enter.
// 3. It paginates the full thread and downloads ig_<id>.json.
//    Feed it to: wxextract instagram fetch --dump ig_<id>.json
(async () => {
  const APP_ID = "936619743392459";
  const tid = (location.pathname.match(/\/direct\/t\/(\d+)/) || [])[1];
  if (!tid) return console.error("Open a DM thread first (…/direct/t/<id>/).");
  const viewer = (document.cookie.match(/ds_user_id=(\d+)/) || [])[1] || "";
  let cursor = null, items = [], users = {}, page = 0;
  while (true) {
    const u = new URL(`https://www.instagram.com/api/v1/direct_v2/threads/${tid}/`);
    u.searchParams.set("limit", "40");
    u.searchParams.set("direction", "older");
    if (cursor) u.searchParams.set("cursor", cursor);
    const r = await fetch(u, { headers: { "X-IG-App-ID": APP_ID }, credentials: "include" });
    if (!r.ok) { console.error("HTTP", r.status); break; }
    const t = ((await r.json()) || {}).thread || {};
    (t.users || []).forEach(x => users[String(x.pk)] = x.full_name || x.username || String(x.pk));
    items = items.concat(t.items || []);
    console.log(`page ${++page}: +${(t.items || []).length} (total ${items.length})`);
    if (!t.has_older || !t.oldest_cursor) break;
    cursor = t.oldest_cursor;
    await new Promise(s => setTimeout(s, 1500 + Math.random() * 1500));
  }
  const blob = new Blob(
    [JSON.stringify({ thread_id: tid, viewer_id: viewer, users, items }, null, 2)],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ig_${tid}.json`;
  a.click();
  console.log(`done — ${items.length} items → ig_${tid}.json`);
})();
