// ===== config =====
const CONTRACT = "con_sbtxian";                // merged SBT + traits
const API_BASE = "http://127.0.0.1:5000";         // Flask server
const RPC_URL  = "https://testnet.xian.org";      // testnet (or mainnet URL)
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

let walletInfo = null;   // { address, truncatedAddress, ... } from XianWalletUtils
let last = null;         // last /api/compare_traits payload
let updating = false;    // simple click guard

function setStatus(msg){ statusEl.textContent = msg; }
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

// ===== wallet init (required by the helper) =====
XianWalletUtils.init(RPC_URL);

document.addEventListener('xianReady', () => {
  setStatus("Wallet bridge ready. Click Connect.");
});

// ===== connect flow using XianWalletUtils =====
async function connectWallet(){
  try{
    const info = await XianWalletUtils.requestWalletInfo();
    walletInfo = info; // includes .address and .truncatedAddress
    addrTag.textContent = `address: ${info.truncatedAddress || info.address || '—'}`;
    netTag.textContent  = `network: testnet`;
    setStatus(`Connected: ${info.truncatedAddress || info.address}`);
  }catch(e){
    console.error(e);
    alert("Wallet not detected or not responding. Ensure the extension allows localhost/127.0.0.1 and you're on testnet.");
  }
}

// ===== compare & update =====
async function doCompare(){
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

    const hasScoreDiff = !!(data.diffs && data.diffs.Score);
    const walletMatches = !!(walletInfo && walletInfo.address === data.address);

    if (hasScoreDiff) {
      setStatus(
        `Difference in Score: DB=${data.diffs.Score.off_chain} vs Chain=${data.diffs.Score.on_chain}` +
        (walletMatches ? "" : " — Connect the same address to update.")
      );
      btnRefresh.style.display = "inline-block";
      btnUpdate.style.display  = "inline-block";
      btnUpdate.disabled = !walletMatches;   // STRICT: only allow if same address
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

async function updateOnChain(){
  if (updating) return;
  if (!last || !last.address){ alert("Compare first."); return; }
  if (!walletInfo){ await connectWallet(); }
  if (!walletInfo){ return; }

  // STRICT: the connected wallet MUST match the compared address
  if (walletInfo.address !== last.address){
    alert(
      `Connected wallet ${walletInfo.address} does not match the address being updated (${last.address}).\n` +
      `Please connect the correct wallet and try again.`
    );
    return;
  }

  const newScore = String(last.offchain?.Score ?? 0);

  try{
    updating = true;
    btnUpdate.disabled = true;
    setStatus("Sending update_trait…");

    // Single transaction ONLY: update_trait("Score", <db score>)
    const tx = await XianWalletUtils.sendTransaction(
      CONTRACT, "update_trait", { key: "Score", value: newScore }, 120
    );
    console.log("update_trait tx status:", tx);

    alert("Update submitted. We’ll re-check in a moment.");
    setTimeout(doCompare, 2000);
  }catch(e){
    console.error(e);
    alert("Failed to send transaction. Confirm the wallet prompt and ensure you’re on testnet.");
  }finally{
    updating = false;
  }
}

// ===== wire up (bind once) =====
btnConnect.addEventListener("click", connectWallet);
btnCompare.addEventListener("click", doCompare);
btnUpdate .addEventListener("click", updateOnChain);
btnRefresh.addEventListener("click", doCompare);

// On load: neutral state
setStatus("No wallet detected yet. Click Connect.");
