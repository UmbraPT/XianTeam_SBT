// Guard against double load
if (window.__SBT_APP_INIT__) {
  console.warn("app.js already initialized — skipping duplicate wiring.");
} else {
  window.__SBT_APP_INIT__ = true;

  // ------- config -------
  const CONTRACT     = "con_sbtxian";             // << your merged SBT+traits contract
  const API_BASE     = "http://127.0.0.1:5000";   // Flask API
  const RPC_URL      = "https://testnet.xian.org";
  const STAMP_LIMIT  = 25;                        // cap stamps for update_trait
  const KEYS = ["Score","Tier","Stake Duration","DEX Volume", "Total Sent XIAN"];

  // ===== DOM =====
  const btnConnect = document.getElementById("btnConnect");
  const btnCompare = document.getElementById("btnCompare");
  let  btnUpdate  = document.getElementById("btnUpdate");
  const btnRefresh = document.getElementById("btnRefresh");
  const addrInput  = document.getElementById("addr");
  const addrTag    = document.getElementById("addrTag");
  const netTag     = document.getElementById("netTag");
  const statusEl   = document.getElementById("status");
  const tableEl    = document.getElementById("table");
  const short = (a) => (a ? `${a.slice(0,6)}…${a.slice(-4)}` : "—");

  let walletInfo = null;   // { address, truncatedAddress, ... }
  let last = null;         // last /api/compare_traits payload
  let updating = false;    // re-entrancy lock
  let lastClickAt = 0;     // debounce timestamp

  const setStatus = (msg) => { statusEl.textContent = msg; };

  function iconForTrait(k){
    const map = {
      "Score":"⚡", "Tier":"🏅", "Stake Duration":"⏳",
      "DEX Volume":"📈"
    };
    return map[k] || "★";
  }

  function humanSeconds(s){
    const n = Number(s||0);
    const d = Math.floor(n/86400), h = Math.floor((n%86400)/3600), m = Math.floor((n%3600)/60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m`;
    return `${n|0}s`;
  }
  function fmtNum(x, digits=2){
    const n = Number(x||0);
    return n.toLocaleString(undefined, { maximumFractionDigits: digits });
  }

  function pretty(k, v){
    if (v === "" || v === undefined || v === null) return "—";
    if (k === "Stake Duration")           return humanSeconds(v);
    if (k === "DEX Volume")               return fmtNum(v, 4);
    if (k === "Total Sent XIAN")          return fmtNum(v, 4);
    return v; // Score or anything else
  }

  function renderTable(data){
    const rows = [];
    rows.push(`<div class="tr h"><div>Trait</div><div>Off‑chain (DB)</div><div>On‑chain</div></div>`);
    let i = 0;
    for (const k of KEYS){
      const rawDb = data.offchain?.[k];
      const rawOn = data.onchain?.[k];
      const dbv   = pretty(k, rawDb);
      const onv   = pretty(k, rawOn);
      const diff = (k === "Score") && String(dbv) !== String(onv);
      rows.push(
        `<div class="tr anim ${diff ? "diff" : ""}" style="--i:${i++}">
           <div class="trait"><span class="ico">${iconForTrait(k)}</span>${k}</div>
           <div>${dbv}</div>
           <div>${onv}</div>
         </div>`
      );
    }
    tableEl.innerHTML = rows.join("");
  }

  // Wallet bridge
  XianWalletUtils.init(RPC_URL);
  document.addEventListener("xianReady", () => setStatus("Wallet bridge ready. Click Connect."));

  // -------- tiers & badges (jungle) --------
  function tierFromScore(score){
    const s = Number(score || 0);
    if (s < 500)    return { key:"leafling",          name:"Leafling",            range:"< 500",        file:"leafling.png" };
    if (s < 1500)   return { key:"vine-crawler",      name:"Vine Crawler",        range:"500–1,500",    file:"vine-crawler.png" };
    if (s < 3000)   return { key:"canopy-dweller",    name:"Canopy Dweller",      range:"1,500–3,000",  file:"canopy-dweller.png" };
    if (s < 5000)   return { key:"rainkeeper",        name:"Rainkeeper",          range:"3,000–5,000",  file:"rainkeeper.png" };
    if (s < 10000)  return { key:"jaguar-fang",       name:"Jaguar Fang",         range:"5,000–10,000", file:"jaguar-fang.png" };
    return             { key:"spirit-of-the-jungle",  name:"Spirit of the Jungle",range:"10,000+",      file:"spirit-of-the-jungle.png" };
  }

  function applyBadge(score){
    const tier = tierFromScore(score);
    const img  = document.getElementById("userBadge");
    const name = document.getElementById("tierName");
    const rng  = document.getElementById("tierRange");
    if (img)  img.src  = `/assets/badges/${tier.file}`;
    if (name) name.textContent = tier.name;
    if (rng)  rng.textContent  = `Tier • ${tier.range}`;
  }

  // navbar scroll state (stronger glass when scrolled)
  const barEl = document.querySelector('.bar');
  const onScroll = () => {
    if (window.scrollY > 4) barEl.classList.add('scrolled');
    else barEl.classList.remove('scrolled');
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  async function connectWallet(){
    try{
      const info = await XianWalletUtils.requestWalletInfo();
      walletInfo = info;
      addrTag.textContent = `address: ${info.truncatedAddress || short(info.address)}`;
      netTag.textContent  = `network: testnet`;
      setStatus(`Connected: ${info.truncatedAddress || info.address}`);
      btnConnect.style.display = "none";
    }catch(e){
      console.error(e);
      alert("Wallet not detected or not responding. Allow localhost/127.0.0.1 and use testnet.");
    }
  }

  async function doCompare(e){
    e?.preventDefault?.();

    const addr = (addrInput.value.trim() || walletInfo?.address || "").trim();
    if (!addr){ alert("Enter an address or connect wallet first."); return; }

    setStatus("Comparing…");
    statusEl.classList.remove("error");
    statusEl.classList.add("live");
    btnUpdate.style.display = "none";
    btnRefresh.style.display = "none";
    btnUpdate.disabled = true;
    tableEl.classList.add("loading");
    tableEl.innerHTML = `<div class="spinner"></div>`;

    try{
      const res = await fetch(`${API_BASE}/api/compare_traits?address=${encodeURIComponent(addr)}`);
      const data = await res.json();
      last = data;

      renderTable(data);
      tableEl.classList.remove("loading");

      const hasScoreDiff  = !!(data.diffs && data.diffs.Score);
      const walletMatches = !!(walletInfo && walletInfo.address === data.address);

      if (hasScoreDiff) {
        setStatus(
          `Difference in Score: DB=${data.diffs.Score.off_chain} vs Chain=${data.diffs.Score.on_chain}` +
          (walletMatches ? "" : " — Connect the same address to update.")
        );
        btnRefresh.style.display = "inline-block";

        // --- SINGLE-SHOT REBIND to prevent double prompts ---
        const fresh = btnUpdate.cloneNode(true);
        btnUpdate.replaceWith(fresh);
        btnUpdate = fresh;

        btnUpdate.style.display = "inline-block";
        btnUpdate.disabled = !walletMatches;
        btnUpdate.classList.toggle("pulse", walletMatches);

        if (walletMatches) {
          btnUpdate.addEventListener("click", updateOnChain, { once: true });
        }
      }
    }catch(e){
      console.error(e);
      setStatus("Failed to compare traits (API error).");
      statusEl.classList.remove("live");
      statusEl.classList.add("error");
      tableEl.classList.remove("loading");
    }
  }

  async function updateOnChain(e){
    console.log("updateOnChain clicked");
    e?.preventDefault?.();

    // strong debounce
    const now = Date.now();
    if (now - lastClickAt < 800) return;
    lastClickAt = now;

    if (updating) return;
    if (!last || !last.address){ alert("Compare first."); return; }
    if (!walletInfo){ await connectWallet(); if (!walletInfo) return; }

    // STRICT: only the holder can update
    if (walletInfo.address !== last.address){
      alert(`Connected wallet ${walletInfo.address} does not match ${last.address}. Connect the correct wallet.`);
      return;
    }

    const newScore = String(last.offchain?.Score ?? 0);

    try{
      updating = true;
      btnUpdate.disabled = true;
      btnUpdate.style.pointerEvents = "none";
      setStatus("Sending update_trait…");

      const tx = await XianWalletUtils.sendTransaction(
        CONTRACT, "update_trait", { key: "Score", value: newScore }, STAMP_LIMIT
      );
      console.log("update_trait tx status:", tx);

      alert("Update submitted. Re‑checking in 2s…");
      setTimeout(doCompare, 2000);
    }catch(e){
      console.error(e);
      alert("Failed to send transaction. Confirm the wallet prompt and ensure you’re on testnet.");
    }finally{
      updating = false;
      btnUpdate.style.pointerEvents = "";
    }
  }

  // Wire up
  btnConnect.addEventListener("click", connectWallet);
  btnCompare.addEventListener("click", doCompare);
  btnRefresh.addEventListener("click", doCompare);
  document.getElementById("scrollToApp")?.addEventListener("click", () => {
    document.getElementById("appCard")?.scrollIntoView({ behavior: "smooth" });
  });

  // Initial table skeleton
  tableEl.innerHTML = [
    `<div class="tr h"><div>Trait</div><div>Off-chain (DB)</div><div>On-chain</div></div>`,
    ...["Score","Stake Duration","DEX Volume","Total Sent XIAN"]
      .map(k => `<div class="tr"><div>${k}</div><div>—</div><div>—</div></div>`)
  ].join("");
}
