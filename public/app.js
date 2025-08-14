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

  // ------- DOM -------
  const addrTag   = document.getElementById("addrTag");
  const netTag    = document.getElementById("netTag");
  const statusEl  = document.getElementById("status");
  const btnConnect= document.getElementById("btnConnect");
  const btnCompare= document.getElementById("btnCompare");
  let   btnUpdate = document.getElementById("btnUpdate");
  const btnRefresh= document.getElementById("btnRefresh");
  const addrInput = document.getElementById("addr");
  const tableEl   = document.getElementById("table");
  const scrollToApp = document.getElementById("scrollToApp");

  let walletInfo = null;   // { address, truncatedAddress }
  let last = null;         // latest compare payload
  let updating = false;
  let lastClickAt = 0;

  const setStatus = m => statusEl.textContent = m;
  const short = v => (v ? `${v.slice(0,6)}…${v.slice(-4)}` : "—");

  function renderTable(data){
    const rows = [];
    rows.push(`<div class="tr h"><div>Trait</div><div>Off‑chain (DB)</div><div>On‑chain</div></div>`);
    for (const k of KEYS){
      const dbv = data.offchain?.[k] ?? "";
      const onv = data.onchain?.[k] ?? "";
      const diff = (k === "Score") && String(dbv) !== String(onv);
      rows.push(`<div class="tr ${diff ? "diff" : ""}"><div>${k}</div><div>${dbv}</div><div>${onv}</div></div>`);
    }
    tableEl.innerHTML = rows.join("");
  }

  // Wallet bridge
  XianWalletUtils.init(RPC_URL);
  document.addEventListener("xianReady", () => setStatus("Wallet bridge ready. Click Connect."));

  async function connectWallet(){
    try{
      const info = await XianWalletUtils.requestWalletInfo();
      walletInfo = info;
      addrTag.textContent = `address: ${info.truncatedAddress || short(info.address)}`;
      netTag.textContent  = `network: testnet`;
      setStatus(`Connected: ${info.truncatedAddress || short(info.address)}`);
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
    btnUpdate.style.display = "none";
    btnRefresh.style.display = "none";
    btnUpdate.disabled = true;
    tableEl.innerHTML = "";

    try{
      const res = await fetch(`${API_BASE}/api/compare_traits?address=${encodeURIComponent(addr)}`);
      const data = await res.json();
      last = data;

      renderTable(data);

      const hasScoreDiff  = !!(data.diffs && data.diffs.Score);
      const walletMatches = !!(walletInfo && walletInfo.address === data.address);

      if (hasScoreDiff) {
        setStatus(
          `Difference in Score: DB=${data.diffs.Score.off_chain} vs Chain=${data.diffs.Score.on_chain}` +
          (walletMatches ? "" : " — Connect the same address to update.")
        );
        btnRefresh.style.display = "inline-block";

        // Single‑shot rebind to avoid double prompts
        const fresh = btnUpdate.cloneNode(true);
        btnUpdate.replaceWith(fresh);
        btnUpdate = fresh;

        btnUpdate.style.display = "inline-block";
        btnUpdate.disabled = !walletMatches;
        if (walletMatches) {
          btnUpdate.addEventListener("click", updateOnChain, { once: true });
        }
      } else {
        setStatus("No differences in Score.");
        btnRefresh.style.display = "inline-block";
        btnUpdate.style.display  = "none";
        btnUpdate.disabled = true;
      }
    }catch(e){
      console.error(e);
      setStatus("Failed to compare traits (API error).");
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
