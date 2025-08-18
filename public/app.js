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
  const KEYS = ["Score","Tier","Stake Duration","DEX Volume","Game Wins","Bots Created","Pulse Influence"];

  // ===== DOM =====
  const btnConnect = document.getElementById("btnConnect");
  const btnCompare = document.getElementById("btnCompare");
  const btnUpdate  = document.getElementById("btnUpdate");
  const btnRefresh = document.getElementById("btnRefresh");
  const addrInput  = document.getElementById("addr");
  const addrTag    = document.getElementById("addrTag");
  const netTag     = document.getElementById("netTag");
  const statusEl   = document.getElementById("status");
  const tableEl    = document.getElementById("table");

  let walletInfo = null;   // { address, truncatedAddress, ... }
  let last = null;         // last /api/compare_traits payload
  let updating = false;    // re-entrancy lock
  let lastClickAt = 0;     // debounce timestamp

  const setStatus = (msg) => { statusEl.textContent = msg; };

  function iconForTrait(k){
    const map = {
      "Score":"⚡", "Tier":"🏅", "Stake Duration":"⏳",
      "DEX Volume":"📈", "Game Wins":"🎮", "Bots Created":"🤖", "Pulse Influence":"📣"
    };
    return map[k] || "★";
  }

  function renderTable(data){
    const rows = [];
    rows.push(`<div class="tr h"><div>Trait</div><div>Off‑chain (DB)</div><div>On‑chain</div></div>`);
    let i = 0;
    for (const k of KEYS){
      const dbv = data.offchain?.[k] ?? "";
      const onv = data.onchain?.[k] ?? "";
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
        btnUpdate.style.display  = "inline-block";
        btnUpdate.disabled = !walletMatches;     // STRICT: only if same address
        btnUpdate.classList.toggle("pulse", walletMatches);
      } else {
        setStatus("No differences in Score.");
        btnRefresh.style.display = "inline-block";
        btnUpdate.style.display  = "none";
        btnUpdate.disabled = true;
        btnUpdate.classList.remove("pulse");
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
    `<div class="tr h"><div>Trait</div><div>Off‑chain (DB)</div><div>On‑chain</div></div>`,
    ...["Score","Tier","Stake Duration","DEX Volume","Game Wins","Bots Created","Pulse Influence"]
      .map(k => `<div class="tr"><div>${k}</div><div>—</div><div>—</div></div>`)
  ].join("");
}
