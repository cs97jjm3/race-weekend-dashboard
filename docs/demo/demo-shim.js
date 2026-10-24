/* Stands in for the dashboard program on the project website, so the real dashboard page
   can play the made-up demo race in any browser. Built by tools/build_web_demo.py. */
(function () {
  const realFetch = window.fetch.bind(window);
  const json = (data, status) => Promise.resolve(new Response(JSON.stringify(data),
    {status: status || 200, headers: {"Content-Type": "application/json"}}));
  let demo = null, started = 0;
  const loading = realFetch("frames.json").then(r => r.json()).then(d => { demo = d; started = performance.now(); });
  const HOLD = 30;   // seconds to sit on the finish before playing again
  function frame() {
    const n = demo.frames.length, secs = (performance.now() - started) / 1000;
    if (secs > n + HOLD) started = performance.now();
    const i = Math.min(n - 1, Math.floor((performance.now() - started) / 1000));
    const v = Object.assign({}, demo.frames[i]);
    v.events = demo.events.slice(0, v.evN).reverse().slice(0, 80);
    v.viewer = "home";
    return v;
  }
  const notHere = what => ({error: what + " looks things up online, so it's in the real dashboard rather than this demo."});
  window.fetch = function (url, opts) {
    const u = typeof url === "string" ? url : url.url;
    if (u.startsWith("/state")) return loading.then(() => json(frame()));
    if (u.startsWith("/track/")) return realFetch("track.json");
    if (u.startsWith("/weekend")) return json({error: "Between sessions, the real dashboard shows the weekend timetable, a countdown and the last result here."});
    if (u.startsWith("/history/")) return json(notHere("Driver, team and race history"));
    if (u.startsWith("/predictions")) return json({error: "The predictions game is in the real dashboard."});
    if (u.startsWith("/ai/") || u.startsWith("/share/") || u.startsWith("/radio")) return json({error: "Not in the demo"});
    return realFetch(url, opts);
  };
  document.addEventListener("DOMContentLoaded", () => {
    const s = document.createElement("style");
    s.textContent = `#predbtn{display:none!important}
      .demoribbon{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:60;background:#c8f031;color:#0b0d10;
        font:600 15px system-ui,sans-serif;padding:7px 16px;border-radius:999px;box-shadow:0 6px 20px rgba(0,0,0,.5)}
      .demoribbon a{color:#0b0d10}`;
    document.head.appendChild(s);
    const r = document.createElement("div");
    r.className = "demoribbon";
    r.innerHTML = 'Demo: a made-up race, sped up 15×. <a href="/" target="_top">About the project</a>';
    document.body.appendChild(r);
  });
})();
