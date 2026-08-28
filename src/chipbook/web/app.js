
"use strict";

/* Empty when the page was opened from a device other than the computer
the program runs on. Then the gate comes first: the code off the laptop
screen traded for a token. On the laptop the token arrives ready and
there is no gate. */
let TOKEN = "__TOKEN__";
// THESE TWO NAMES STAY AS THEY ARE, and it is not an oversight. Browser
// storage is keyed by origin, so a phone that was paired with the older
// build finds its own entries again under these exact names. Renaming them
// would silently log the phone out - and worse, ABANDON any job typed at
// the machine while the laptop was off, because the queue below is where
// those wait. A job somebody typed is worth more than a tidy name.
const CODE_KEY = "chipbook-kod";

const state = {
  jobs: [],
  words: [],
  chosen: null,
  mode: "empty",          // empty | detail | new
  // YES by default - on the laptop everything is to work as before even
  // for a moment, before the answer about the program state arrives.
  onThisComputer: true,
  customer: "",             // chosen customer - empty means "everybody"
  customerList: false,   // whether the left panel shows customers instead of entries
  customers: [],
  searchMode: "manual",  // manual | ai  (the switch beside the search field)
  conversation: [],            // bubbles in AI mode: {who:"me"|"ai", msg}
  originalQuestion: "",   // this is what we answer; the rest are clarifications
  clarifications: [],     // pairs [the model's question, the person's answer]
  lastModelQuestion: "",
  lastPersonQuestion: "",   // context for questions like "and with what cutter?"
  // PINNING THE CONVERSATION TO ONE JOB (asked for by the end user).
  // As long as something stands here, EVERY further question goes to
  // that entry and the search is not touched at all.
  // The server and the database could do this for ten days - the window
  // never sent the number, so the whole mechanism was dead.
  pinned: null,        // {id, name, customer, material} or null
  // WHETHER CHIPBOOK IS WAITING FOR A PERSON TO POINT AT A JOB.
  // Only when this stands true does a click on a row CHOOSE the job
  // instead of opening its card. Outside AI mode nothing changes.
  waitingForChoice: false,
  suggestions: {customer:[], material:[]},
  draft: null,
  draftFiles: [],
  previews: {},
};

/* --------------------------------------------------------------- helpers */

const ACCENTS = {"ą":"a","ć":"c","ę":"e","ł":"l","ń":"n","ó":"o","ś":"s","ź":"z","ż":"z",
                "Ą":"A","Ć":"C","Ę":"E","Ł":"L","Ń":"N","Ó":"O","Ś":"S","Ź":"Z","Ż":"Z"};

function stripAccents(msg){
  let out = "";
  for (const z of msg) out += (ACCENTS[z] !== undefined ? ACCENTS[z] : z);
  return out;
}

function esc(msg){
  return String(msg == null ? "" : msg)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

function ranges(msg, words){
  // we count hit positions on the text with the accents stripped - the
  // mapping is 1:1, so the indexes match the original
  const flat = stripAccents(msg).toLowerCase();
  const marks = [];
  for (const s of words){
    if (!s) continue;
    let fromIndex = 0;
    while (true){
      const i = flat.indexOf(s, fromIndex);
      if (i < 0) break;
      marks.push([i, i + s.length]);
      fromIndex = i + Math.max(1, s.length);
    }
  }
  marks.sort((a,b) => a[0]-b[0]);
  const merged = [];
  for (const z of marks){
    const last = merged[merged.length-1];
    if (last && z[0] <= last[1]) last[1] = Math.max(last[1], z[1]);
    else merged.push(z.slice());
  }
  return merged;
}

function highlight(msg, words){
  const marks = ranges(msg, words);
  if (!marks.length) return esc(msg);
  let out = "", previous = 0;
  for (const [a,b] of marks){
    out += esc(msg.slice(previous, a)) + "<mark>" + esc(msg.slice(a,b)) + "</mark>";
    previous = b;
  }
  return out + esc(msg.slice(previous));
}

function snippet(msg, words, maxLength){
  maxLength = maxLength || 190;
  const single = msg.replace(/\s+/g," ").trim();
  const marks = ranges(single, words);
  if (!marks.length || marks[0][0] < maxLength * 0.5) return highlight(single.slice(0, maxLength), words) + (single.length > maxLength ? "…" : "");
  const start = Math.max(0, marks[0][0] - 50);
  const piece = single.slice(start, start + maxLength);
  return "…" + highlight(piece, words) + (start + maxLength < single.length ? "…" : "");
}

function formatWhen(iso){
  if (!iso) return "";
  const t = iso.replace(" ", "T");
  const d = new Date(t);
  if (isNaN(d)) return iso;
  const nowDate = new Date();
  const day = (x) => x.getFullYear()+"-"+x.getMonth()+"-"+x.getDate();
  const hour = String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");
  if (day(d) === day(nowDate)) return "today " + hour;
  const yesterday = new Date(nowDate.getTime() - 864e5);
  if (day(d) === day(yesterday)) return "yesterday " + hour;
  const month = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"];
  const year = d.getFullYear() === nowDate.getFullYear() ? "" : " " + d.getFullYear();
  return d.getDate() + " " + month[d.getMonth()] + year;
}

function plural(n, one, many){
  return n === 1 ? one : many;
}

function humanSize(bytes){
  bytes = Number(bytes) || 0;
  if (bytes < 1024) return bytes + " B";
  const units = ["kB","MB","GB"];
  let w = bytes / 1024, i = 0;
  while (w >= 1024 && i < units.length - 1){ w /= 1024; i++; }
  return (w < 10 ? w.toFixed(1) : Math.round(w)).toString().replace(".", ",") + " " + units[i];
}

function extensionOf(name){
  const parts = String(name || "").split(".");
  if (parts.length < 2) return "file";
  return parts.pop().slice(0, 5);
}

function sendFile(jobIdFor, file, onProgress){
  // XMLHttpRequest and not fetch: only it says how much has gone already.
  // A CAM project can weigh hundreds of MB - without this the user stares
  // at nothing.
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/jobs/" + jobIdFor + "/files");
    x.setRequestHeader("X-Chipbook-Token", TOKEN);
    x.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
    };
    x.onload = () => {
      let data = {};
      try { data = JSON.parse(x.responseText); } catch (e) {}
      if (x.status === 200) resolve(data);
      else reject(new Error(data.error_message || ("Error " + x.status)));
    };
    x.onerror = () => reject(new Error("The connection to the program was cut off."));
    x.send(file);
  });
}

function attachDropZone(el, onFiles){
  const picker = el.querySelector("input[type=file]");
  el.addEventListener("click", (e) => { if (e.target !== picker) picker.click(); });
  picker.addEventListener("change", () => {
    if (picker.files.length) onFiles(Array.from(picker.files));
    picker.value = "";
  });
  ["dragenter","dragover"].forEach(z => el.addEventListener(z, (e) => {
    e.preventDefault(); el.classList.add("over");
  }));
  ["dragleave","dragend"].forEach(z => el.addEventListener(z, () => el.classList.remove("over")));
  el.addEventListener("drop", (e) => {
    e.preventDefault(); el.classList.remove("over");
    const fileList = Array.from(e.dataTransfer.files || []);
    if (fileList.length) onFiles(fileList);
  });
}

function dropZoneHtml(id, description){
  return '<div class="dropzone" id="' + id + '">' +
    '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>' +
    // THIS FIELD HAS NO `accept` AND NO `capture` AND THAT IS A DECISION,
    // NOT AN OVERSIGHT. Measured on an Android with five fields side by
    // side:
    //   no accept                         -> a menu with THREE icons:
    //                                        camera, video, files
    //   accept="image/*,video/*"          -> Google Photos, that is the
    //                                        gallery alone, NO camera
    //   accept="image/*,video/*" +capture -> the same, because Chrome does
    //                                        not know whether to open the
    //                                        camera or the video camera,
    //                                        and falls back to the menu
    //   accept="image/*" +capture         -> the camera at once (no choice)
    //   accept="video/*" +capture         -> the video camera at once
    // So every attempt at narrowing TAKES the camera away from the phone
    // rather than adding it. An empty field gives the most: a photo, a video
    // and a file from one place.
    // WHOEVER WANTS TO CHANGE THIS: repeat that measurement first. Twice in
    // one day I "corrected" this field on the strength of reading and twice
    // it came out worse than before the correction.
    // The label changes as before, because "drop files here" means nothing
    // on a phone.
    // THE LABEL IS TO REMIND WHAT IS POSSIBLE - not merely to describe what
    // the button does. A video could be recorded earlier too, because the
    // same file field does it, but nobody thinks of that unless it is
    // written down. A label is cheaper here than a feature and works the
    // same.
    '<b>' + (state.onThisComputer
              ? "Drop files here, or click to choose"
              : "Take a photo or record a video, or choose from the gallery") + '</b>' +
    '<span class="small">' + esc(state.onThisComputer ? description
              : "the part, the fixture, the finished piece, the machine at work") + '</span>' +
    '<input type="file" multiple hidden></div>';
}

/* How many blocks are visible before "Show all" has to be clicked.
Two - agreed on a preview. */
const VISIBLE_COUNT = 2;
let BLOCK_COUNTER = 0;

/* The button is drawn together with the preview, and the preview is
redrawn every time an entry is opened - that is why we listen on the
whole window instead of hooking on to each button separately. Otherwise
after every redraw one would have to remember to hook up again, and
sooner or later somebody forgets. */
document.addEventListener("click", (ev) => {
  const button = ev.target.closest ? ev.target.closest(".show-more") : null;
  if (!button) return;
  const targetEl = document.getElementById(button.dataset.target);
  if (!targetEl) return;
  const wasHidden = targetEl.hidden;
  targetEl.hidden = !wasHidden;
  button.textContent = wasHidden
    ? "Collapse"
    : ("Show all (" + button.dataset.count + ")");
});

function blockHtml(b){
  const indent = ' style="margin-left:' +
    (Math.min(3, Math.max(0, (b.level || 0) - 1)) * 16) + 'px"';
  if (b.kind === "pairs"){
    if (!b.pairs.length) return '<div class="block"' + indent +
      '><h4>' + esc(b.title) + '</h4></div>';
    return '<div class="block"' + indent + '><h4>' + esc(b.title) + '</h4>' +
      '<div class="table-wrap"><table class="table-xml"><tbody>' +
      b.pairs.map(p =>
        '<tr><td class="field-name">' + esc(p[0]) + '</td><td>' +
        highlight(String(p[1]), state.words) + '</td></tr>').join("") +
      '</tbody></table></div></div>';
  }
  if (b.kind === "table"){
    /* OPERATIONS ONE UNDER ANOTHER, NOT SIDE BY SIDE.
    A turned table used to stand here - field names going down, operations in
    columns. It was made on an explicit wish and was good for a week. We
    changed it because THE END USER said so: he judged that the vertical
    reads better.
    What we lose by it, and this has to be known: comparing operations side
    by side. It used to be visible at a glance that the stock to leave falls
    1.0 -> 0.0 -> 0.0; now one has to scroll down.
    The older version stands in the history of this file, right before the
    commit "feat(ui): setup sheet tables vertical" - it comes back with one
    revert should the comparison turn out to be needed.
    EMPTY FIELDS ARE SKIPPED. In a table an empty cell showed "-", because
    otherwise the columns would drift apart. Vertically there is nothing to
    line up, so a field the file does not hold simply takes no room. */
    const count = b.rows.length;
    const fields = b.columns.slice(1);
    const blocks = b.rows.map((r, i) => {
      const pairs = fields
        .map((k, j) => [k, r[j + 1]])
        .filter(p => String(p[1] == null ? "" : p[1]).trim() !== "");
      return '<div class="op-block"><div class="op-top">' +
        '<span class="op-no">' + (i + 1) + ' of ' + count + '</span>' +
        '<span class="op-title">' +
        highlight(String(r[0] == null ? "" : r[0]), state.words) +
        '</span></div>' +
        pairs.map(p => '<div class="op-pair">' +
          '<span class="op-name">' + esc(p[0]) + '</span>' +
          '<span class="op-value">' +
          highlight(String(p[1]), state.words) + '</span></div>').join("") +
        '</div>';
    });
    const hiddenOnes = blocks.slice(VISIBLE_COUNT).join("");
    const restId = "rest-" + (++BLOCK_COUNTER);
    return '<div class="block"' + indent + '><h4>' + esc(b.title) +
      '<span class="count-in">' + count + '</span></h4>' +
      blocks.slice(0, VISIBLE_COUNT).join("") +
      (hiddenOnes
        ? '<div id="' + restId + '" hidden>' + hiddenOnes + '</div>' +
          '<button type="button" class="show-more" data-target="' + restId +
          '" data-count="' + count + '">Show all (' + count + ')</button>'
        : "") +
      '</div>';
  }
  return "";
}

function previewHtml(z, withTitle){
  const p = state.previews[z.id];
  if (!p) return "";
  let middle = "";
  if (p.kind === "xml" || p.kind === "pdf"){
    middle = (p.blocks || []).map(blockHtml).join("");
    if (!middle) middle = '<div class="preview-notice">This file holds no data to show.</div>';
  } else if (p.kind === "text"){
    middle = '<pre class="file-text">' + p.text_lines.map(l =>
      '<span class="no">' + l[0] + '</span>' + esc(l[1])).join("\n") + '</pre>';
  } else if (p.notice){
    middle = '<div class="preview-notice">' + esc(p.notice) + '</div>';
  }
  return '<div class="file-preview">' +
    (withTitle ? '<h3 class="section-title">From the file ' + esc(p.name) + '</h3>' : '') +
    (p.notice && p.kind !== "error" && p.kind !== "too_big"
       ? '<div class="preview-notice">' + esc(p.notice) + '</div>' : '') +
    middle + '</div>';
}

async function loadPreview(id){
  if (state.previews[id]) return;
  try{ state.previews[id] = await api("/api/files/" + id + "/preview"); }
  catch(e){ state.previews[id] = {kind:"error", name:"", notice:e.message}; }
}

async function loadMainPreviews(w){
  state.previews = {};
  for (const z of (w.attachments || [])){
    if (z.viewable && z.setup_sheet) await loadPreview(z.id);
  }
}

function fileHtml(z){
  return '<div class="file" data-id="' + z.id + '">' +
    '<span class="ext">' + esc(extensionOf(z.name)) + '</span>' +
    '<span class="middle"><div class="name">' + highlight(z.name, state.words) + '</div>' +
      '<div class="small">' + esc(humanSize(z.size_bytes)) +
      (z.present ? "" : " &middot; no longer on disk") + '</div></span>' +
    (z.viewable && !z.setup_sheet
       ? '<button class="btn btn-quiet expand">Show</button>' : '') +
    (z.present ? '<button class="btn btn-light download">Open</button>' : '') +
    // DELETE ONLY ON THIS COMPUTER. From the phone one may append, not
    // delete - a phone is sometimes in a pocket and one touch has no right
    // to throw out somebody's file. The server watches the same
    // independently.
    (state.onThisComputer
       ? '<button class="btn btn-quiet delete-file">Delete</button>' : '') +
    '</div>' +
    (z.viewable && !z.setup_sheet
       ? '<div class="file-preview" id="preview-' + z.id + '" hidden></div>' : '');
}

async function deleteFile(jobNumber, item){
  // CONFIRMATION IS OBLIGATORY. This is the only button in the window
  // that throws out a person's file - and the "Delete" button stands right
  // beside "Open", so a mistake of one centimetre is real.
  // We say OUTRIGHT that the file goes to the Recycle Bin rather than
  // vanishing: that is the difference between "what have I done" and
  // "never mind, I will get it back".
  if (!confirm('Delete the file "' + item.name + '" from this entry?\n\n' +
               'The file goes to the system Recycle Bin - it can be got ' +
               'back from there. The entry itself and the notes stay.')) return;
  try{
    const outcome = await api("/api/files/" + item.id + "/delete",
                            {method:"POST", body:"{}"});
    // WE SAY WHAT REALLY HAPPENED, and not always "deleted". The system
    // Recycle Bin will not always take a file, and then it lies set aside in
    // the data directory - a person has the right to know that at once.
    if (outcome.where === "recycle_bin") toast("The file is in the Recycle Bin: " + item.name);
    else if (outcome.where === "moved")
      toast("The Recycle Bin would not take the file. It was set aside in _deleted.");
    else if (outcome.where === "no_file")
      toast("The entry no longer has it. It was not on disk anyway.");
    else toast("Removed from the entry, but the file stayed on disk.", true);
    await showJob(jobNumber);
  }catch(e){ toast(e.message, true); }
}

async function openFile(id, name){
  // ON A PHONE THE FILE IS TO OPEN HERE, not on the laptop.
  // The rule used to be that the server always opens it, because with one
  // computer the browser could only fetch a copy into Downloads. From a
  // phone that same rule meant "open it at the other end of the workshop" -
  // a photo taken with the phone was opened with the Open button and it
  // opened on the laptop. The rule therefore still holds, but ONLY on this
  // computer.
  if (!state.onThisComputer) return showFile(id, name);
  // We ask THE SERVER to open the file where it lies. The browser can only
  // fetch a copy - and a copy in Downloads serves nobody.
  try{
    await api("/api/files/" + id + "/open", {method:"POST", body:"{}"});
    toast("Opening " + name);
  }catch(e){
    toast(e.message + " Fetching a copy instead of opening it.", true);
    await downloadFile(id, name);
  }
}

async function showFile(id, name){
  // A file to look at on THIS device. The server serves known formats
  // (photos, PDF, text) with their real type, so the browser draws them
  // itself instead of downloading.
  try{
    const response = await fetch("/api/files/" + id + "?show=1",
                            {headers:{"X-Chipbook-Token": TOKEN}});
    if (!response.ok){
      let d = {}; try { d = await response.json(); } catch(e){}
      throw new Error(d.error_message || "The file could not be opened.");
    }
    const url = URL.createObjectURL(await response.blob());
    // ALWAYS IN A NEW TAB, NEVER IN THIS ONE.
    // The first version moved the current tab when Safari blocked a new
    // window - and that was a bug that only showed up on a phone: the file
    // address is temporary, so if somebody added chipbook to the home screen
    // at that moment, the iPhone remembered an address that stopped existing
    // a moment later. The shortcut opened a white page.
    // An ordinary link works where window.open is blocked too.
    const a = document.createElement("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }catch(e){ toast(e.message, true); }
}

async function downloadFile(id, name){
  try{
    const response = await fetch("/api/files/" + id, {headers:{"X-Chipbook-Token": TOKEN}});
    if (!response.ok){
      let d = {}; try { d = await response.json(); } catch(e){}
      throw new Error(d.error_message || "The file could not be opened.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }catch(e){ toast(e.message, true); }
}

let toastTimer = null;
function toast(msg, bad){
  const el = document.getElementById("toast");
  el.className = "toast visible" + (bad ? " bad" : "");
  el.innerHTML = (bad ? "" : '<span class="tick">✓</span>') + "<span>" + esc(msg) + "</span>";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 5200);
}

async function api(path, options){
  options = options || {};
  options.headers = Object.assign({"X-Chipbook-Token": TOKEN}, options.headers || {});
  if (options.body) options.headers["Content-Type"] = "application/json";
  let response;
  try{
    response = await fetch(path, options);
  }catch(e){
    throw new Error("The program stopped answering. Is the console window still open?");
  }
  let data;
  try{ data = await response.json(); }
  catch(e){ throw new Error("The server gave an answer that cannot be read."); }
  if (!response.ok) throw new Error(data.error_message || ("Error " + response.status));
  return data;
}

/* ------------------------------------------------------------ list */

async function showCustomers(){
  try{
    const data = await api("/api/customers");
    state.customers = data.customers || [];
    state.customerList = true;
    drawList();
  }catch(e){ toast(e.message, true); }
}

async function chooseCustomer(customer){
  state.customer = customer;
  state.customerList = false;
  // CHOOSING A CUSTOMER CLEARS THE PHRASE. Left in place it would mean
  // "search for that word AT THIS customer", which nobody asked for, and
  // the results would look empty for no reason.
  document.getElementById("search-field").value = "";
  await refreshList();
}

function drawCustomers(){
  const el = document.getElementById("list");
  const count = document.getElementById("results-count");
  document.getElementById("list-heading").textContent = "Customers";
  count.textContent = state.customers.length
    ? state.customers.length + " " +
      plural(state.customers.length, "customer", "customers") : "";

  if (!state.customers.length){
    el.innerHTML = emptyState("icon-book", "There is no customer yet",
      "Customers come out of the entries - out of the Customer field. Make the first entry and one will appear here.");
    return;
  }
  el.innerHTML = state.customers.map(k =>
    '<button class="row' + (state.customer.toLowerCase() === k.customer.toLowerCase() ? " chosen" : "") +
    '" data-customer="' + esc(k.customer) + '">' +
      '<div class="row-top">' +
        '<span class="badge">' + icon("folder") + esc(k.customer) + '</span>' +
        // THE NUMBER OF JOBS BESIDE THE NAME: without it a customer typed once
        // with a typo looks the same as one who has been worked for over a year.
        // "ACME 41" next to "ACEM 1" says outright where the mistake is.
        '<span class="data">' + k.count + " " +
          plural(k.count, "job", "jobs") + '</span>' +
      '</div>' +
    '</button>').join("");
  el.querySelectorAll(".row").forEach(b =>
    b.addEventListener("click", () => chooseCustomer(b.dataset.customer)));
}

function drawList(){
  refreshCustomersButton();
  if (state.customerList) return drawCustomers();
  // THE LIST HEADING SAYS WHAT TO DO. It stands here rather than where the
  // answer arrives, because the list is sometimes drawn afresh from several
  // places - and the text must not drift apart from the state.
  const heading = document.getElementById("list-heading");
  if (heading){
    heading.classList.toggle("choice", !!state.waitingForChoice);
    if (state.waitingForChoice){
      heading.textContent = "Click the job you mean";
    }
  }
  const el = document.getElementById("list");
  const count = document.getElementById("results-count");
  count.textContent = state.jobs.length
    ? state.jobs.length + " " + plural(state.jobs.length, "entry", "entries")
    : "";

  if (!state.jobs.length){
    const searched = document.getElementById("search-field").value.trim();
    el.innerHTML = emptyState(
      searched ? "icon-search" : "icon-book",
      searched ? "There is no such thing in the database"
            : "The database is still empty",
      searched
        ? "I do not guess and I do not suggest something similar. Try a shorter fragment - three letters from the middle of a word are enough."
        : "The first entry is enough to start with: the material and two sentences about what hurt."
    );
    return;
  }

  el.innerHTML = state.jobs.map(w => {
    const meta = [];
    if (w.customer)   meta.push("<span>" + icon("folder") + highlight(w.customer, state.words) + "</span>");
    if (w.material) meta.push("<span>" + icon("cutter") + highlight(w.material, state.words) + "</span>");
    // THE ROW SAYS FOR ITSELF THAT IT CAN BE POINTED AT - an orange edge and
    // the word "Choose". Only when chipbook really is waiting for a choice;
    // in an ordinary search the list looks as it always did.
    const forChoice = !!state.waitingForChoice;
    return '<button class="row' + (state.chosen && state.chosen.id === w.id ? " chosen" : "") +
      (forChoice ? " candidate" : "") +
      '" data-id="' + w.id + '">' +
        '<div class="row-top">' +
          '<span class="badge">' + highlight(w.name || w.material, state.words) + '</span>' +
          (forChoice ? '<span class="choose">Choose \u2192</span>' : '') +
          '<span class="data">' + esc(formatWhen(w.updated_at || w.created_at)) + '</span>' +
        '</div>' +
        '<div class="snippet">' + snippet(w.notes || "", state.words) + '</div>' +
        (meta.length ? '<div class="meta-row">' + meta.join("") + '</div>' : '') +
      '</button>';
  }).join("");

  el.querySelectorAll(".row").forEach(b => {
    // A CLICK ON A ROW ALWAYS OPENS THE PREVIEW - including when chipbook is
    // waiting for a choice. Reported: before pointing at a job, a person
    // wants to SEE it first. The previous version took one thing away in
    // order to give the other.
    b.addEventListener("click", () => showJob(Number(b.dataset.id)));
    const choose = b.querySelector(".choose");
    if (choose){
      choose.addEventListener("click", (e) => {
        // Without stopping it the click would reach the row underneath TOO and
        // the program would open the card at the very moment it starts the
        // conversation.
        e.stopPropagation();
        pointAtJob(Number(b.dataset.id));
      });
    }
  });
}

function pointAtJob(index){
  // A person has pointed at a job among several. From now on we talk about
  // it: askAI(number) writes its name in as the person's message, asks
  // the model about THAT ONE entry and pins the bar at the top.
  // WE ANSWER AT ONCE, we do not make them ask a second time - the person
  // has already asked their question, only the pointing was missing.
  state.waitingForChoice = false;
  // THE BAR IS PINNED AT THE MOMENT OF CHOICE, and not only after a
  // successful answer. CAUGHT IN USE: the model did not answer (it timed
  // out after 180 s) and THE PINNING WAS LOST ALONG WITH THE ANSWER - even
  // though the person had said outright which job they meant. A person's
  // choice has no right to depend on whether the model made it in time.
  const chosenJob = (state.jobs || []).filter(
    w => String(w.id) === String(index))[0];
  if (chosenJob){
    state.pinned = {id: chosenJob.id, name: chosenJob.name,
                      customer: chosenJob.customer,
                      material: chosenJob.material};
    refreshConversationBar();
  }
  askAI(index);
}

function icon(kind){
  const svgAttrs = 'width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  if (kind === "folder") return '<svg ' + svgAttrs + '><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
  if (kind === "machine") return '<svg ' + svgAttrs + '><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 20h8M12 18v2"/></svg>';
  return '<svg ' + svgAttrs + '><path d="M12 3v10"/><path d="M8 13h8l-1.5 7h-5z"/></svg>';
}

function emptyState(iconName, title, description){
  const svgAttrs = 'width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
  const svg = iconName === "icon-search"
    ? '<svg ' + svgAttrs + '><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>'
    : '<svg ' + svgAttrs + '><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
  return '<div class="empty-state"><div class="icon-big">' + svg + '</div>' +
         '<h3>' + esc(title) + '</h3><p>' + esc(description) + '</p></div>';
}

/* ---------------------------------------------------------------- detail */

function drawRight(){
  const el = document.getElementById("right-column");
  // On a narrow screen the two panels do not fit side by side, so the CSS
  // shows ONLY one at a time - and it has to know which. Instead of a second
  // state in the JavaScript we expose the one that already exists.
  // On a large screen this changes nothing: both panels stand as they
  // stand.
  document.body.dataset.mode = state.mode;
  if (state.mode === "new") return drawForm(el);
  if (state.mode === "detail" && state.chosen) return drawDetail(el, state.chosen);
  el.innerHTML =
    '<div class="heading-panel"><h2>Entry</h2></div>' +
    emptyState("icon-book", "Nothing chosen",
           "Click an entry in the list on the left to open it - or make a new one with the button at the top.");
}

function fieldPairs(name, val){
  const empty_fields = !val;
  return '<div class="field"><dt>' + esc(name) + '</dt>' +
         '<dd class="' + (empty_fields ? "empty" : "") + '">' +
         (empty_fields ? "not given" : highlight(val, state.words)) + '</dd></div>';
}

function drawDetail(el, w){
  const atts = w.attachments || [];
  el.innerHTML =
    '<div class="heading-panel"><h2>Entry no. ' + w.id + '</h2>' +
      '<span class="count">' + esc(w.folder || "") + '</span>' +
      '<span class="delete-zone" id="delete-zone"></span></div>' +
    '<div class="content"><div class="content-narrow">' +
      '<div id="block-fields">' + jobFieldsHtml(w) + '</div>' +
      '<div id="block-notes"><h3 class="section-title">Notes</h3>' +
      '<div class="notes">' + highlight(w.notes || "", state.words) + '</div></div>' +
      atts.filter(z => z.viewable && z.setup_sheet)
         .map(z => '<div class="note-block">' + previewHtml(z, true) + '</div>').join("") +
      '<div class="note-block">' +
        '<h3 class="section-title">Files' +
          (atts.length ? ' <span style="font-weight:400;letter-spacing:0;text-transform:none">(' + atts.length + ')</span>' : '') +
        '</h3>' +
        '<div id="file-list">' + atts.map(fileHtml).join("") + '</div>' +
        dropZoneHtml("drop-detail",
          "STEP, G-code, CAM project, PDF, setup sheet - a copy goes into the entry folder") +
        '<div class="bar-actions" style="border-top:0;padding-top:12px;margin-top:12px">' +
          '<button class="btn btn-light" id="btn-folder">Open the entry folder</button>' +
          '<span class="hint">' + esc(w.data_dir || "") + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="note-block">' +
        '<h3 class="section-title">Append to this entry</h3>' +
        '<div class="group wide">' +
          '<textarea id="note" style="min-height:110px" placeholder="What else came out of this job? An appended note goes to the end of the notes with today\u0027s date - it overwrites nothing."></textarea>' +
        '</div>' +
        '<div class="bar-actions">' +
          '<button class="btn btn-accent" id="btn-append">Append</button>' +
        '</div>' +
      '</div>' +
    '</div></div>';

  drawDeleting(el, w);
  attachDropZone(el.querySelector("#drop-detail"),
                   (fileList) => addFiles(w.id, fileList));
  el.querySelectorAll("#file-list .file").forEach(row => {
    const item = atts.find(z => String(z.id) === row.dataset.id);
    if (!item) return;
    const button = row.querySelector(".download");
    if (button) button.addEventListener("click", () => openFile(item.id, item.name));
    const clear = row.querySelector(".delete-file");
    if (clear) clear.addEventListener("click", () => deleteFile(w.id, item));
    const expand = row.querySelector(".expand");
    if (expand) expand.addEventListener("click", async () => {
      const slot = el.querySelector("#preview-" + item.id);
      if (!slot) return;
      if (!slot.hidden){ slot.hidden = true; expand.textContent = "Show"; return; }
      expand.textContent = "Reading...";
      await loadPreview(item.id);
      slot.innerHTML = previewHtml(item, false);
      slot.hidden = false;
      expand.textContent = "Hide";
    });
  });
  el.querySelector("#btn-folder").addEventListener("click", async () => {
    try { await api("/api/jobs/" + w.id + "/folder", {method:"POST", body:"{}"}); }
    catch (e) { toast(e.message, true); }
  });

  const field = el.querySelector("#note");
  el.querySelector("#btn-append").addEventListener("click", () => appendNote(w.id, field));
}

function jobFieldsHtml(w){
  return '<div class="title"><h1>' + highlight(w.name || w.material, state.words) + '</h1></div>' +
    '<div class="subtitle">Added ' + esc(formatWhen(w.created_at)) +
      (w.updated_at && w.updated_at !== w.created_at ? " · updated_at " + esc(formatWhen(w.updated_at)) : "") +
    '</div>' +
    '<dl class="grid">' +
      fieldPairs("Customer", w.customer) +
      fieldPairs("Material", w.material) +
      /* The order number. fieldPairs highlights the searched words by itself, so
      typing the number into the search lights it up yellow just as the material
      is lit. REPORTED: the number was saved into the database, but the preview
      did not show it - which from the user's point of view meant it was not
      there at all. */
      fieldPairs("Order number", w.order_number) +
    '</dl>';
}

/* Editing the fields of an entry. It does NOT concern the notes - for
those there is the appended note, and that still overwrites nothing. */
function drawFieldEditing(el, w){
  const block = el.querySelector("#block-fields");
  if (!block) return;
  // The notes move up into the top block while editing, so we hide the one
  // at the bottom - otherwise the same content would stand on screen twice.
  const readable = el.querySelector("#block-notes");
  if (readable) readable.hidden = true;
  block.innerHTML =
    '<div class="form-row">' +
      '<div class="group"><label for="e-name">Name<span class="star">*</span></label>' +
        '<input type="text" id="e-name" autocomplete="off" value="' + esc(w.name || "") + '"></div>' +
      '<div class="group"><label for="e-customer">Customer<span class="star">*</span></label>' +
        '<input type="text" id="e-customer" list="d-customer" autocomplete="off" value="' + esc(w.customer || "") + '">' +
        optionList("d-customer", state.suggestions.customer) + '</div>' +
    '</div>' +
    '<div class="form-row">' +
      '<div class="group"><label for="e-material">Material<span class="star">*</span></label>' +
        '<input type="text" id="e-material" list="d-material2" autocomplete="off" value="' + esc(w.material || "") + '">' +
        optionList("d-material2", state.suggestions.material) + '</div>' +
      '<div class="group"><label for="e-order_number">Order number</label>' +
        '<input type="text" id="e-order_number" autocomplete="off" value="' + esc(w.order_number || "") + '"></div>' +
    '</div>' +
    '<div class="form-row">' +
      '<div class="group wide"><label for="e-notes">Notes</label>' +
        '<textarea id="e-notes" style="min-height:150px">' + esc(w.notes || "") + '</textarea></div>' +
    '</div>' +
    '<div class="bar-actions" style="border-top:0;padding-top:0">' +
      '<button class="btn btn-accent" id="e-save">Save changes</button>' +
      '<button class="btn btn-quiet" id="e-cancel">Cancel</button>' +
    '</div>';
  block.querySelector("#e-cancel").addEventListener("click", () => {
    block.innerHTML = jobFieldsHtml(w);
    if (readable) readable.hidden = false;
    drawDeleting(el, w);
  });
  block.querySelector("#e-save").addEventListener("click", async () => {
    try{
      const changed = await api("/api/jobs/" + w.id + "/fields", {
        method: "POST",
        body: JSON.stringify({
          name: block.querySelector("#e-name").value,
          customer: block.querySelector("#e-customer").value,
          material: block.querySelector("#e-material").value,
          order_number: block.querySelector("#e-order_number").value,
          notes: block.querySelector("#e-notes").value
        })
      });
      state.chosen = changed;
      await refreshList();
      drawRight();
      toast("Entry corrected.");
    }catch(e){ toast(e.message, true); }
  });
  block.querySelector("#e-name").focus();
}

function drawDeleting(el, w){
  const zone = el.querySelector("#delete-zone");
  if (!zone) return;
  zone.innerHTML =
    '<button class="btn btn-light" id="btn-edit">Edit</button>' +
    '<button class="btn btn-quiet" id="btn-delete">Delete the entry</button>';
  zone.querySelector("#btn-edit").addEventListener("click",
    () => drawFieldEditing(el, w));
  zone.querySelector("#btn-delete").addEventListener("click", () => {
    // two steps instead of a system dialog: the question appears in place of
    // the button, so it cannot be clicked by reflex
    zone.innerHTML =
      '<span class="question">Delete this entry?</span>' +
      '<button class="btn btn-red" id="yes-button">Yes, to the Recycle Bin</button>' +
      '<button class="btn btn-quiet" id="no-button">No</button>';
    zone.querySelector("#no-button").addEventListener("click", () => drawDeleting(el, w));
    zone.querySelector("#yes-button").addEventListener("click", () => deleteJob(w));
  });
}

const WHERE_IT_WENT = {
  recycle_bin: "The entry folder went to the system Recycle Bin - it can be got back from there.",
  moved: "The system Recycle Bin was unavailable, so the entry folder now lies in the _deleted subdirectory.",
  no_folder: "This entry had no folder on disk.",
  left_in_place: "CAREFUL: the folder could not be moved, it stayed on disk where it was."
};

async function deleteJob(w){
  try{
    const outcome = await api("/api/jobs/" + w.id + "/delete",
                            {method:"POST", body:"{}"});
    state.chosen = null;
    state.mode = "empty";
    await refreshList();
    await refreshState();
    drawRight();
    toast("Entry no. " + outcome.id + " deleted. " + (WHERE_IT_WENT[outcome.where] || ""));
  }catch(e){ toast(e.message, true); }
}

async function addFiles(id, fileList){
  const list = document.getElementById("file-list");
  for (const file of fileList){
    const row = document.createElement("div");
    row.className = "file fading";
    row.innerHTML =
      '<span class="ext">' + esc(extensionOf(file.name)) + '</span>' +
      '<span class="middle"><div class="name">' + esc(file.name) + '</div>' +
      '<div class="small">copying ' + esc(humanSize(file.size)) + '...</div>' +
      '<div class="bar-progress"><i></i></div></span>';
    if (list) list.appendChild(row);
    const bar = row.querySelector(".bar-progress i");
    try{
      const response = await sendFile(id, file, (count) => {
        bar.style.width = Math.round(count * 100) + "%";
      });
      state.chosen = response.job;
      await loadMainPreviews(response.job);
    }catch(e){
      row.querySelector(".small").textContent = e.message;
      toast(e.message, true);
      return;
    }
  }
  drawRight();
  await refreshList(true);
  toast(fileList.length === 1 ? "The file was attached to the entry."
                           : fileList.length + " files attached to the entry.");
}

async function appendNote(id, field){
  const msg = field.value.trim();
  if (!msg) { field.focus(); return toast("The note is empty.", true); }
  try{
    const w = await api("/api/jobs/" + id + "/notes",
                        {method:"POST", body: JSON.stringify({msg})});
    state.chosen = w;
    await loadMainPreviews(w);
    drawRight();
    await refreshList(true);
    toast("Appended to entry no. " + id + ".");
  }catch(e){ toast(e.message, true); }
}

/* ----------------------------------------------------------------- form */

function optionList(id, values){
  return '<datalist id="' + id + '">' +
    (values || []).map(v => '<option value="' + esc(v) + '"></option>').join("") +
    '</datalist>';
}

function drawForm(el){
  const s = state.draft || {name:"",customer:"",material:"",order_number:"",notes:""};
  s.draftFilesHtml = state.draftFiles.map((p, i) =>
    '<div class="file" data-i="' + i + '">' +
      '<span class="ext">' + esc(extensionOf(p.name)) + '</span>' +
      '<span class="middle"><div class="name">' + esc(p.name) + '</div>' +
      '<div class="small">' + esc(humanSize(p.size)) + ' &middot; will be copied on saving</div></span>' +
      '<button class="btn btn-quiet delete">Delete</button></div>').join("");
  el.innerHTML =
    '<div class="heading-panel"><h2>New entry</h2></div>' +
    '<div class="content"><div class="content-narrow">' +
      '<div id="form-error" hidden></div>' +
      '<div class="form-row">' +
        '<div class="group"><label for="f-name">Name<span class="star">*</span></label>' +
          '<input type="text" id="f-name" autocomplete="off" value="' + esc(s.name) + '"></div>' +
        '<div class="group"><label for="f-customer">Customer<span class="star">*</span></label>' +
          '<input type="text" id="f-customer" list="d-customer" autocomplete="off" value="' + esc(s.customer) + '">' +
          optionList("d-customer", state.suggestions.customer) + '</div>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="group"><label for="f-material">Material<span class="star">*</span></label>' +
          '<input type="text" id="f-material" list="d-material" autocomplete="off" value="' + esc(s.material) + '">' +
          optionList("d-material", state.suggestions.material) + '</div>' +
        '<div class="group"><label for="f-order_number">Order number</label>' +
          '<input type="text" id="f-order_number" autocomplete="off" value="' + esc(s.order_number || "") + '"></div>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="group wide"><label for="f-notes">Notes</label>' +
          '<textarea id="f-notes">' + esc(s.notes) + '</textarea>' +
          '</div>' +
      '</div>' +
      '<div class="form-row"><div class="group wide">' +
        '<label>Files</label>' +
        '<div id="draft-files">' + s.draftFilesHtml + '</div>' +
        dropZoneHtml("drop-new",
          "STEP, G-code, CAM project, PDF, setup sheet - a copy will go into the entry folder") +
      '</div></div>' +
      /* CANCEL ON THE LEFT, SAVE ON THE RIGHT (reported). On a phone the thumb
      reaches the right-hand side, and saving is the movement that gets
      repeated. */
      '<div class="bar-actions bar-actions-right">' +
        '<button class="btn btn-quiet" id="btn-cancel">Cancel</button>' +
        '<button class="btn btn-accent" id="btn-save">Save the entry</button>' +
      '</div>' +
    '</div></div>';

  const fields = ["name","customer","material","order_number","notes"];
  const readFields = () => {
    const data = {};
    fields.forEach(p => data[p] = el.querySelector("#f-" + p).value);
    return data;
  };
  fields.forEach(p => el.querySelector("#f-" + p)
    .addEventListener("input", () => { state.draft = readFields(); }));

  attachDropZone(el.querySelector("#drop-new"), (fileList) => {
    state.draft = readFields();
    state.draftFiles = state.draftFiles.concat(fileList);
    drawRight();
  });
  el.querySelectorAll("#draft-files .delete").forEach(button => {
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      state.draft = readFields();
      state.draftFiles.splice(Number(button.closest(".file").dataset.i), 1);
      drawRight();
    });
  });

  el.querySelector("#btn-save").addEventListener("click", () => saveJob(readFields()));
  el.querySelector("#btn-cancel").addEventListener("click", () => {
    state.draft = null; state.draftFiles = [];
    state.mode = state.chosen ? "detail" : "empty"; drawRight();
  });
  el.querySelector("#f-name").focus();
}

function formError(msg){
  const el = document.getElementById("form-error");
  if (!el) return;
  el.hidden = false;
  el.className = "form-error-box";
  el.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/></svg><span>' + esc(msg) + '</span>';
}

async function saveJob(data){
  try{
    const w = await api("/api/jobs", {method:"POST", body: JSON.stringify(data)});
    // The entry is already safe in the database. We add the files AFTER it -
    // were the copying to fail, the content of the entry is still not lost.
    let lastJob = w;
    if (state.draftFiles.length){
      const field = document.querySelector("#draft-files");
      for (let i = 0; i < state.draftFiles.length; i++){
        const file = state.draftFiles[i];
        const row = field && field.querySelector('[data-i="' + i + '"]');
        const small = row && row.querySelector(".small");
        if (small) small.textContent = "copying " + humanSize(file.size) + "...";
        try{
          const response = await sendFile(w.id, file, (count) => {
            if (small) small.textContent = "copying... " + Math.round(count * 100) + "%";
          });
          lastJob = response.job;
        }catch(e){
          toast("The entry was saved, but the file " + file.name
              + " was not copied: "
                + e.message, true);
        }
      }
    }
    state.draft = null;
    state.draftFiles = [];
    state.chosen = lastJob;
    await loadMainPreviews(lastJob);
    state.mode = "detail";
    document.getElementById("search-field").value = "";
    await refreshList();
    await refreshState();
    drawRight();
    toast("Saved as entry no. " + w.id + ".");
  }catch(e){
    formError(e.message);
  }
}

/* ------------------------------------------------------------ data */

async function showJob(id){
  try{
    state.chosen = await api("/api/jobs/" + id);
    await loadMainPreviews(state.chosen);
    state.mode = "detail";
    drawRight();
    drawList();
  }catch(e){ toast(e.message, true); }
}

function drawCorrections(corrections, skipped, forms){
  const el = document.getElementById("correction");
  const sentences = [];
  if (forms && forms.length){
    // The program searched for ANOTHER FORM of the word - a person is to know about it.
    const description = forms.map(f =>
      '"' + esc(f[0]) + '" → ' + f[1].map(s => '"' + esc(s) + '"').join(", ")
    ).join("; ");
    sentences.push('That word is not in the database in this form, so I searched ' +
                'for its other forms: ' + description + '.');
  }
  if (corrections && corrections.length){
    const description = corrections.map(p => '"' + esc(p[0]) + '" → "' + esc(p[1]) + '"').join(", ");
    sentences.push('That word was not in the database, so I corrected a typo: ' +
                description + '. The results are for the corrected version.');
  }
  if (skipped && skipped.length){
    // Never quietly: a person is to know what the program did NOT search for.
    const description = skipped.map(s => '"' + esc(s) + '"').join(", ");
    sentences.push('These words are in no entry, so I searched without them: ' +
                description + '.');
  }
  if (!sentences.length){ el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/></svg>' +
    '<span>' + sentences.join(' ') + '</span>';
}

async function refreshList(keepChoice){
  const phrase = document.getElementById("search-field").value.trim();
  // The model's answer concerns a question that has just stopped applying.
  // Left on screen it would pretend to describe the new list.
  showAnswer("", "");
  showChatField(false);
  try{
    // SEARCHING TAKES THE CUSTOMER FILTER OFF. When a person types in the
    // search field they want to search the WHOLE database - a silent filter
    // from an earlier click would hide results for no reason visible on
    // screen.
    if (phrase) state.customer = "";
    state.customerList = false;
    const data = await api("/api/jobs?q=" + encodeURIComponent(phrase) +
                           "&customer=" + encodeURIComponent(state.customer));
    state.jobs = data.jobs || [];
    state.words = (data.words || []).filter(s => s.length >= 2);
    document.getElementById("list-heading").textContent =
      data.mode === "search" ? "Results"
      : data.mode === "customer" ? "Customer: " + data.customer
      : "All jobs";
    drawCorrections(data.corrections, data.skipped, data.forms);
    if (!keepChoice && state.mode === "detail" && state.chosen &&
        !state.jobs.some(w => w.id === state.chosen.id)){
      // the chosen entry dropped out of the list - we do not close it, we only
      // stop highlighting it; a person does not like content vanishing from
      // under their hand
    }
    drawList();
  }catch(e){ toast(e.message, true); }
}

async function refreshState(){
  try{
    const s = await api("/api/status");
    document.getElementById("counter").textContent =
      s.job_count + " " + plural(s.job_count, "entry", "entries") + " in the database";
    state.onThisComputer = s.is_local !== false;
    // The window hides what we do not do from a phone. The distinction goes
    // by DEVICE, not by screen width: a narrow window on the laptop is to have
    // everything, and a tablet on the shop floor is not.
    document.body.dataset.remote = state.onThisComputer ? "" : "1";
    const address = document.getElementById("phone-address");
    // The address without "http://" and without a trailing slash - typed by
    // hand on a phone, the figures alone are copied and the browser adds the
    // rest itself. The code for the gate stands beside it: those two things
    // are copied out together, so they stand together rather than in two
    // corners of the window.
    address.textContent =
      (s.phone_address || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
    address.hidden = !(s.network_on && s.is_local && s.phone_address);
    document.getElementById("btn-new-code").hidden =
      !(s.network_on && s.is_local);
    showVersionAlert(s.stale);
    state.suggestions = await api("/api/suggestions");
  }catch(e){ /* the counter is decoration - we do not bother anybody with it */ }
}

function showVersionAlert(stale){
  // The engine of the program is loaded once, at startup. When a newer
  // version lies on disk, new buttons call into the old engine - and the
  // user gets a message that cannot be understood. So we tell them outright
  // what to do.
  const el = document.getElementById("alarm");
  if (!el) return;
  if (!stale || !stale.length){ el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 9v5M12 17.5v.5"/><path d="M10.3 3.9 2.5 17.4A1.8 1.8 0 0 0 4 20h16a1.8 1.8 0 0 0 1.5-2.6L13.7 3.9a1.8 1.8 0 0 0-3.4 0z"/></svg>' +
    // THE INSTRUCTION HAS TO MATCH HOW THIS VERSION IS ACTUALLY STARTED.
    // An earlier wording named a starter file and a desktop icon that this
    // package does not ship, so the person reading it had nothing to click.
    // An instruction that cannot be carried out is worse than none - it
    // sends somebody looking for a file that is not there.
    '<span><b>The program was updated on disk, but an older version is still running.</b> ' +
    'Start it again: stop chipbook - <b>Ctrl+C</b> in the window it was ' +
    'started from, or end the <b>python</b> process in Task Manager - and ' +
    'start it once more. Until then some buttons may not work.</span>';
}

/* ------------------------------------------------------------ start */

/* ------------------------------------------- manual mode / AI mode

Two modes of one search. Manual mode stays EXACTLY as it was - it
searches at every character typed. AI mode cannot work that way: one
question is tens of seconds of computing, so it asks only after Enter.

The sources visible under an answer are an ordinary list of entries -
the same cards as always. The model does not choose them and cannot add
to that list an entry the search did not find. */

function showAnswer(cssClass, html){
  const el = document.getElementById("answer-ai");
  if (!html){ el.hidden = true; return; }
  el.hidden = false;
  el.className = "answer-ai" + (cssClass ? " " + cssClass : "");
  el.innerHTML = html;
}

/* ============ THE CONVERSATION WINDOW: ONE PLACE THE STATE CHANGES =====
Folding and unfolding goes ONLY through here. A quarter-second lock,
because one tap can raise several events in a row (pointerdown, mouseup,
click) and without it the window folded and unfolded again at once. */
let aiWindowLock = 0;

function setAiWindow(opened){
  const nowMs = Date.now();
  if (nowMs < aiWindowLock) return;
  const wasOpen = document.body.dataset.aiWindow === "open";
  if (wasOpen === opened) return;
  aiWindowLock = nowMs + 250;
  document.body.dataset.aiWindow = opened ? "open" : "collapsed";
  refreshAiBar();
  const field = document.getElementById("chat-input");
  // preventScroll: without it the browser scrolls the page to the field and
  // the whole page jumps.
  if (opened) { try { field.focus({preventScroll:true}); } catch(e) { field.focus(); } }
  else field.blur();
}

function refreshAiBar(){
  const count = (state.conversation || []).filter(d => d.who === "me").length;
  const p = state.pinned;
  document.getElementById("ai-what").textContent =
    p ? ("Talking about " + (p.name || ("entry " + p.id)))
      : "Conversation with the AI";
  document.getElementById("ai-count").textContent =
    count ? ("· " + count + (count === 1 ? " question" : " questions")) : "";
  document.getElementById("ai-back").textContent =
    document.body.dataset.aiWindow === "open" ? "collapse \u2191" : "open \u2193";
}

function setMode(mode){
  state.searchMode = mode;
  document.querySelectorAll("#modes button").forEach(b => {
    b.classList.toggle("chosen", b.dataset.mode === mode);
  });
  const field = document.getElementById("search-field");
  const ai = mode === "ai";
  // THE SEARCH FIELD GIVES UP ITS PLACE TO THE BAR. In AI mode it does nothing.
  document.body.dataset.ai = ai ? "1" : "";
  field.placeholder = "Search by material, tool, notes\u2026";
  document.getElementById("btn-search").textContent = "Search";
  resetConversation();
  showAnswer("", "");
  document.getElementById("chat").hidden = !ai;
  if (ai){
    // ONE FIELD FOR THE WHOLE CONVERSATION. Until now the first question fell
    // in the bar and the rest in the field under the bubbles - two places for
    // the same thing.
    showChatField(true);
    document.body.dataset.aiWindow = "collapsed";   // so that setAiWindow does something
    aiWindowLock = 0;
    setAiWindow(true);
  } else {
    showChatField(false);
    document.body.dataset.aiWindow = "collapsed";
    field.focus();
    refreshList();
  }
  refreshFieldHeight();
  refreshAiBar();
}

function refreshFieldHeight(){
  // The field grows downwards ONLY when a person is in it. Click elsewhere
  // and it folds up by itself, with no switching of modes. Reported: to fold
  // it, one had to go back to "Manual".
  // The text is not lost on folding - the field merely stops showing it
  // whole.
  const field = document.getElementById("search-field");
  field.classList.toggle("question",
    state.searchMode === "ai" && document.activeElement === field);
}

function sendSearch(){
  if (state.searchMode === "ai") askAI();
  else refreshList();
}

/* -------------------------------------------------- the conversation with the AI

This is not one question and one answer but A CONVERSATION (asked for):
"the user types and on that basis the model will ask further".

The thing that does NOT change here and cannot: the narrowing is done by
THE SEARCH, not by the model. Every answer a person gives is appended to
the query and we search the whole database afresh. The model gets only
what the search found - that is why this still works at hundreds of
entries, and why "I do not have this in the database" stays possible.

The conversation sits HERE, in the window. The server does not remember
it, the database knows nothing of it - there is no new state to keep. */

function resetConversation(){
  state.conversation = [];
  state.originalQuestion = "";
  state.clarifications = [];
  state.lastModelQuestion = "";
  state.lastPersonQuestion = "";
  state.pinned = null;
  state.waitingForChoice = false;
  refreshConversationBar();
}

function refreshConversationBar(){
  // The bar is a MIRROR of the state, not a memory of its own. We draw it
  // afresh every time, so that it cannot be brought about that one job
  // stands on screen while the questions go to another.
  const convBar = document.getElementById("bar-conversation");
  if (!convBar) return;
  // The bar in the beam mirrors the same state - we refresh both at once,
  // so that two different truths cannot end up on screen.
  if (document.getElementById("ai-what")) refreshAiBar();
  const p = state.pinned;
  if (!p){
    convBar.hidden = true;
    convBar.innerHTML = "";
    return;
  }
  const note = [p.customer, p.material]
    .filter(w => w && String(w).trim()).join(" \u00b7 ");
  convBar.hidden = false;
  convBar.innerHTML =
    '<span class="label">Talking about</span>' +
    '<span class="name">' + esc(p.name || ("entry " + p.id)) +
    "</span>" +
    (note ? '<span class="note">\u00b7 ' + esc(note) +
               "</span>" : "") +
    '<button type="button" class="unpin" ' +
    'title="Stop talking about this job" ' +
    'aria-label="Stop talking about this job">' +
    "&#10005;</button>";
  convBar.querySelector(".unpin").addEventListener("click", () => {
    // WE TAKE OFF THE PINNING ONLY. The bubbles stay - a person sees what
    // they asked about. For clearing the conversation there is a separate
    // "Start over" button, and two buttons doing the same thing would be
    // worse than one.
    state.pinned = null;
    refreshConversationBar();
    conversationField().focus();
  });
}

function drawConversation(note){
  // NO SUGGESTIONS TO CLICK. Buttons with job names worked with two
  // entries and would be useless with two hundred - and above all they
  // pushed an answer forward instead of letting a person say their own.
  // A conversation goes in words.
  const el = document.getElementById("answer-ai");
  const box = document.getElementById("chat");
  if (!state.conversation.length && !note){
    el.hidden = true; box.hidden = true; return;
  }
  box.hidden = false;
  el.hidden = false;
  el.className = "answer-ai conversation";
  const caption = {me: "Your message", ai: "chipbook"};
  // HOW MANY CHARACTERS OF AN ANSWER WE SHOW BEFORE FOLDING IT.
  // A model can write a lecture despite the form. What is to stay on screen
  // is what the person asked about, and the rest one click away. 160
  // characters is about two lines - as much as an honest answer with a
  // figure and a unit takes.
  const LONG_ANSWER_LIMIT = 160;
  el.innerHTML = state.conversation.map((w, index) => {
    const whole = String(w.msg || "");
    const isLong = w.who === "ai" && !w.expanded
                  && whole.length > LONG_ANSWER_LIMIT;
    const content = isLong
      ? whole.slice(0, LONG_ANSWER_LIMIT).trim() + "\u2026" : whole;
    return '<span class="turn ' + w.who + '">' +
      '<span class="who">' + caption[w.who] + "</span>" +
      '<span class="bubble ' + w.who + '">' + esc(content) +
      (isLong
        ? '<button type="button" class="more" data-index="' + index +
          '">show the full answer</button>' : "") +
      "</span>" +
      // THE SOURCE UNDER THE BUBBLE, outside it. Only when it really arrived -
      // we do not pretend the model gave one when it did not.
      (w.who === "ai" && w.source
        ? '<span class="source-beside">from: ' + esc(w.source) +
          (w.sourceConfirmed === false
            ? '<span class="uncertain">I did not find this line in the entry</span>' : "") +
          "</span>" : "") +
      "</span>";
  }).join("") + (note || "");
  el.querySelectorAll(".more").forEach(moreBtn => {
    moreBtn.addEventListener("click", () => {
      const bubble = state.conversation[Number(moreBtn.dataset.index)];
      if (bubble){ bubble.expanded = true; drawConversation(""); }
    });
  });
  el.scrollTop = el.scrollHeight;
}

function showChatField(show){
  const box = document.getElementById("chat-field");
  box.hidden = !show;
  if (show){
    const chat = document.getElementById("chat-input");
    chat.value = "";
    chat.focus();
  }
}

function waitingBubble(msg){
  return '<span class="turn ai"><span class="bubble ai waiting">' +
         esc(msg) + "</span></span>";
}

function conversationField(){
  // The first question falls in the bar at the top, the rest of the
  // conversation goes on in the field under the bubbles. We take whichever
  // is visible at this moment.
  return document.getElementById("chat-field").hidden
    ? document.getElementById("search-field")
    : document.getElementById("chat-input");
}

async function askAI(jobId){
  const field = conversationField();
  const typed = field.value.trim();

  if (jobId){
    const chosen = state.jobs.filter(w => String(w.id) === String(jobId))[0];
    state.conversation.push({who:"me", msg: (chosen && chosen.name) || ("jobId " + jobId)});
  } else {
    if (!typed) return;
    if (!state.originalQuestion){
      // A new question. If a previous one has already been asked, its text
      // stays as CONTEXT for searching - thanks to that "and with what cutter?"
      // still hits the same job. We answer the NEW question, though.
      if (state.lastPersonQuestion){
        state.clarifications.push(["", state.lastPersonQuestion]);
      }
      state.originalQuestion = typed;
    } else {
      state.clarifications.push([state.lastModelQuestion, typed]);
    }
    state.lastPersonQuestion = typed;
    state.conversation.push({who:"me", msg: typed});
    field.value = "";
    // The question has gone - the large field in the bar has nothing left to
    // show and folds by itself. The conversation goes on in the field under
    // the bubbles.
    if (field.id === "search-field") field.blur();
    refreshFieldHeight();
  }

  // The panel is a conversation from the first message sent, and not only
  // from the answer - otherwise it would say "All jobs" for the whole wait.
  document.getElementById("list-heading").textContent =
    "Conversation with the AI";
  document.getElementById("correction").hidden = true;
  // Short and with no scaring about time. The end user's machine is better
  // than ours and an answer can come at once - there is no point announcing
  // a wait that may not happen.
  drawConversation(waitingBubble("Composing an answer\u2026"));

  try{
    const content = {question: state.originalQuestion,
                   clarifications: state.clarifications};
    // A PINNED JOB BEATS EVERYTHING. As long as the bar stands, the question
    // goes to it and the search is not called at all - that is the whole
    // request.
    if (state.pinned) content.job = state.pinned.id;
    else if (jobId) content.job = jobId;
    const data = await api("/api/ask", {
      method: "POST", body: JSON.stringify(content),
    });

    state.jobs = data.jobs || [];
    state.words = [];
    // EVERY ANSWER STARTS BY TAKING THE CHOICE MODE OFF. We switch it on
    // below if there were several jobs. Thanks to that one cannot be left with
    // "Click a job" over a list that has nothing left to choose from.
    state.waitingForChoice = false;
    document.getElementById("list-heading").textContent =
    "Conversation with the AI";
    drawList();

    if (data.kind === "several"){
      const sentence = differencesText(data);
      state.lastModelQuestion = sentence;
      state.conversation.push({who:"ai", msg: sentence});
      // FROM NOW ON A CLICK ON A ROW CHOOSES A JOB. The list has to be drawn
      // ONCE MORE, because above it was drawn before we knew there were several
      // jobs.
      state.waitingForChoice = true;
      drawList();
      drawConversation("");
      showChatField(true);
      return;
    }
    if (data.kind === "none"){
      state.conversation.push({who:"ai", msg: "I do not have this in the database."});
    } else {
      state.conversation.push({who:"ai", msg: data.text,
                         source: data.source || "",
                         sourceConfirmed:
                           data.source_confirmed !== false});
    }
    // The answer has come. The field STAYS - a person may ask about something
    // more (reported). The next message will be a new question, but the
    // previous one stays as context for searching, so that "and with what
    // cutter?" hits the same job.
    // THE ANSWER CAME FROM ONE JOB - from now on we are talking about IT, and
    // the bar at the top says which. We pin only on the kind "one": on an
    // "error" there is no telling whether the model read that entry at all,
    // and on "none" there is nothing to pin.
    if (data.kind === "one" && !state.pinned){
      const found = (data.jobs || [])[0];
      if (found){
        state.pinned = {id: found.id, name: found.name,
                          customer: found.customer,
                          material: found.material};
      }
    }
    refreshConversationBar();
    state.originalQuestion = "";
    state.lastModelQuestion = "";
    showChatField(true);
    drawConversation("");
  }catch(e){
    state.conversation.push({who:"ai", msg: e.message});
    showChatField(true);
    drawConversation("");
  }
}

function differencesText(data){
  // The sentence is composed by THE WINDOW, not the model. An attempt to
  // hand this to the model ended with it inventing a question about "a hole
  // of a shape other than round" - a notion no entry holds.
  //
  // We write out THE ACTUAL VALUES, not just "they differ in material": a
  // person is to be given something to choose by, not a riddle.
  const labels = {name:"", customer:"customer", material:"material"};
  const text_lines = (data.candidates || []).map(k => {
    const parts = (data.differences || [])
      .filter(p => p !== "name" && k[p])
      .map(p => labels[p] + " " + k[p]);
    return "• " + (k.name || ("entry " + k.id)) +
           (parts.length ? " (" + parts.join(", ") + ")" : "");
  });
  const count = state.jobs.length;
  return "I have " + count + " " + plural(count, "such entry", "such entries")
         + " in the database:\n" +
         text_lines.join("\n") +
         "\n\nThey are below - click the one you mean, or add something "
         + "that tells them apart.";
}

function toMainScreen(){
  // A return to the state the program starts in: manual mode, an empty
  // search, no conversation, nothing chosen, the full list of entries.
  // Reported: there was no road back at all other than closing the program
  // and starting it again.
  resetConversation();
  showChatField(false);
  showAnswer("", "");
  document.getElementById("search-field").value = "";
  state.chosen = null;
  state.mode = "empty";
  state.draft = null;
  state.draftFiles = [];
  setMode("manual");         // refreshes the list by itself
  drawRight();
  document.getElementById("search-field").blur();
  window.scrollTo(0, 0);
}

document.getElementById("brand").addEventListener("click", toMainScreen);
document.getElementById("brand").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toMainScreen(); }
});

document.getElementById("modes").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-mode]");
  if (btn) setMode(btn.dataset.mode);
});

let searchTimer = null;
document.getElementById("search-field").addEventListener("input", () => {
  if (state.searchMode === "ai") return;   // in AI mode the question waits for Enter
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshList, 160);
});

document.getElementById("search-field").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey) return;   // Shift+Enter = a new line
  e.preventDefault();                            // the textarea would add one
  sendSearch();
});

document.getElementById("btn-search").addEventListener("click", sendSearch);
document.getElementById("search-field").addEventListener("focus", refreshFieldHeight);
document.getElementById("search-field").addEventListener("blur", refreshFieldHeight);

document.getElementById("chat-send").addEventListener("click", () => askAI());
document.getElementById("chat-new").addEventListener("click", () => {
  // "Start over" ends the conversation but STAYS in AI mode. It used to
  // take the field away and send one back to the search bar - and since that
  // change the bar is something else and there is nowhere to type there.
  resetConversation();
  drawConversation("");
  refreshList();
  showChatField(true);
  refreshAiBar();
});
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey) return;   // Shift+Enter = a new line
  e.preventDefault();
  askAI();
});

/* ============ FOLDING THE CONVERSATION WINDOW ============
THE DECISION AT THE RELEASE OF THE BUTTON, NOT AT THE PRESS. On the press
the window vanished while the finger was still down, and the release could
bring it back. The whole gesture counts: press and release in the same
place, without moving - a movement is scrolling the list with a finger,
not a tap. */
let gestureFrom = null, gestureX = 0, gestureY = 0;
document.addEventListener("pointerdown", (e) => {
  gestureFrom = e.target; gestureX = e.clientX; gestureY = e.clientY;
}, true);
document.addEventListener("pointerup", (e) => {
  const start = gestureFrom; gestureFrom = null;
  const targetEl = e.target;
  if (!start || !targetEl || !targetEl.closest) return;
  if (Math.abs(e.clientX - gestureX) > 10 || Math.abs(e.clientY - gestureY) > 10) return;
  if (start !== targetEl && !(start.contains && start.contains(targetEl))) return;
  if (targetEl.closest("#bar-ai")){
    setAiWindow(document.body.dataset.aiWindow !== "open");
    return;
  }
  if (state.searchMode !== "ai") return;
  if (document.body.dataset.aiWindow !== "open") return;
  if (targetEl.closest("#ai-window") || targetEl.closest("#modes")) return;
  setAiWindow(false);
}, true);

document.getElementById("btn-collapse-ai").addEventListener("click", () => setAiWindow(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.searchMode === "ai") setAiWindow(false);
});

document.getElementById("btn-new").addEventListener("click", newJob);

document.getElementById("btn-customers").addEventListener("click", () => {
  // ONE BUTTON BOTH WAYS. Whether you are looking at customers or at the
  // jobs of one of them - the same button goes back to all the entries.
  // A separate "back" button would be a second element doing the same
  // thing.
  if (state.customerList || state.customer){
    state.customerList = false;
    state.customer = "";
    refreshList(true);
  } else {
    showCustomers();
  }
  refreshCustomersButton();
});

document.getElementById("btn-back-customers").addEventListener("click", () => {
  // WE GO BACK TO THE CUSTOMERS, but we do NOT take the filter off - thanks
  // to that the list shows which customer one was at (a highlighted row).
  // Taking the filter off here would wipe the trace of where a person came
  // from.
  showCustomers();
});

function refreshCustomersButton(){
  // THE LABEL IS TO SAY WHAT THE BUTTON WILL DO, not where you are. One
  // button doing two things under the same label is a trap: a person clicks
  // "Customers" expecting customers and comes back to all the entries.
  //
  // THREE STATES, TWO BUTTONS (corrected after a remark). The first version
  // had one button and from the jobs of one customer there was no way back
  // TO THE CUSTOMERS - only to all the entries, and then "Customers" again.
  // Two clicks and a detour for a mistake in one click.
  //
  //   all jobs           -> [Customers]
  //   the customer list  -> [All jobs]
  //   a customer's jobs  -> [<-] [All jobs]
  const b = document.getElementById("btn-customers");
  const back = document.getElementById("btn-back-customers");
  if (!b) return;
  const somewhere = state.customerList || state.customer;
  b.textContent = somewhere ? "All jobs" : "Customers";
  b.title = somewhere ? "Take the filter off and show all jobs"
                   : "Show the customer list";
  // The arrow ONLY at a customer's jobs. On the customer list itself there
  // is nowhere to go back to, and a button that does nothing is worse than
  // no button.
  if (back) back.hidden = !(state.customer && !state.customerList);
}

function newJob(){
  state.mode = "new";
  drawRight();
}

/* -------------------------------------------------- closing the program

You close the window with the cross - the program ends, like any normal
program. No watching for inactivity: chipbook may stand open for half a
day untouched and will still be working.

WITH THE NETWORK SWITCHED ON THE SERVER IGNORES THIS GOODBYE - we still
send it, because the window need not know which mode the program stands
in.

pagehide comes on a page refresh TOO, so the server does not end at once -
it waits a dozen seconds, and the new page cancels the shutdown with its
first request. keepalive:true is necessary here: without it the browser
would break the request off during closing. */
window.addEventListener("pagehide", () => {
  try{
    fetch("/api/shutdown", {
      method: "POST",
      headers: {"X-Chipbook-Token": TOKEN, "Content-Type": "application/json"},
      body: "{}",
      keepalive: true,
    });
  }catch(e){ /* the window is going anyway - there is nobody to show it to */ }
});

/* ------------------------------------------------------------- the gate

The phone does not get a token along with the page - it has to give the
code shown in the window on the laptop. We remember THE CODE, not the
token: the token is created anew at every start of the program, so a
remembered one would stop working after the first restart and a person
would be copying the code out endlessly. The code is permanent, so it is
typed once. */
async function codeToToken(typedCode){
  const response = await fetch("/api/session", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({code: typedCode}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error_message || "The code does not match.");
  return data.token;
}

async function passGate(){
  if (TOKEN) return true;
  let remembered = null;
  try{ remembered = localStorage.getItem(CODE_KEY); }catch(e){}
  if (remembered){
    try{
      TOKEN = await codeToToken(remembered);
      return true;
    }catch(e){
      try{ localStorage.removeItem(CODE_KEY); }catch(e2){}
    }
  }
  document.getElementById("gate").hidden = false;
  document.getElementById("gate-code").focus();
  return false;
}

document.getElementById("gate-ok").addEventListener("click", async () => {
  const field = document.getElementById("gate-code");
  const gateError = document.getElementById("gate-error");
  const typedCode = field.value.trim();
  gateError.textContent = "";
  try{
    TOKEN = await codeToToken(typedCode);
  }catch(e){
    gateError.textContent = e.message;
    field.select();
    return;
  }
  try{ localStorage.setItem(CODE_KEY, typedCode); }catch(e){}
  document.getElementById("gate").hidden = true;
  await run();
});

document.getElementById("gate-code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("gate-ok").click();
});

/* ------------------------------------------------------------- the code

THE CODE DOES NOT STAND ON SCREEN. It appears only after the button is
clicked, inside the button itself, and goes on the first click anywhere
else or on a page refresh. Whoever did not manage to copy it out clicks
again and gets another, because every click draws a new one.
The program changes the token at the same time, so phones that were
already in are thrown out. Our window gets a new token in the answer and
swaps it in place - with no page reload, because a reload would blow the
fresh code off the screen before a person had managed to look at it. */
document.getElementById("btn-new-code").addEventListener("click", async (e) => {
  e.stopPropagation();
  const btn = document.getElementById("btn-new-code");
  let data;
  try{
    data = await api("/api/pairing-code", {method: "POST", body: "{}"});
  }catch(err){ toast(err.message, true); return; }
  if (data.token) TOKEN = data.token;
  btn.textContent = data.code;
  btn.classList.add("shown");
  const hide = () => {
    btn.textContent = "New code";
    btn.classList.remove("shown");
    document.removeEventListener("click", hide, true);
  };
  document.addEventListener("click", hide, true);
});

async function run(){
  await refreshState();
  await refreshList();
  drawRight();
}

/* REMEMBERING CHIPBOOK IN THE PHONE.
Without this the icon on the home screen is only a shortcut to an address -
with the laptop shut it opens nothing. With it the browser keeps a copy of
the window at its own end and serves it when the laptop is not there.
IT WORKS EXCLUSIVELY OVER A SECURED CONNECTION, that is at the phone. The
window on the laptop runs on 127.0.0.1 and there it is not needed - a
laptop that is shut has nobody to show a window to.
A failure does NOT stop the program: chipbook is to work just as before
also when the browser cannot do this. */
async function rememberInPhone(){
  const version = "window " + WINDOW_VERSION;
  if (!("serviceWorker" in navigator)){
    console.log(version + " - this browser cannot remember the page");
    return;
  }
  /* ON THE LAPTOP WE STORE NO COPY, AND THAT MATTERS MORE THAN IT LOOKS.
  CAUGHT IN USE: the window on the laptop started getting an OLD copy of the
  page with an old token, and after a restart of chipbook the token is
  different - and the window said "No access to this database", with no road
  out.
  The laptop has no reason to store a copy: chipbook on it is either running
  or there is nothing to show. The copy serves the phone EXCLUSIVELY.
  We also delete what has already managed to be stored - otherwise machines
  this happened on would be left with a broken window forever. */
  if (location.hostname === "127.0.0.1" || location.hostname === "localhost"){
    try{
      const list = await navigator.serviceWorker.getRegistrations();
      for (const r of list) await r.unregister();
      if (window.caches){
        for (const n of await caches.keys()) await caches.delete(n);
      }
    }catch(e){ /* nothing to delete - so much the better */ }
    return;
  }
  if (location.protocol !== "https:"){
    console.log(version + " - the connection is NOT secured, " +
                "the page will not be remembered");
    return;
  }
  try{
    await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;
    let count = 0;
    if (window.caches){
      for (const name of await caches.keys()){
        count += (await (await caches.open(name)).keys()).length;
      }
    }
    const controlled = !!navigator.serviceWorker.controller;
    console.log(version + " - remembered: " + (controlled ? "YES" : "NOT YET") +
                ", stored parts: " + count);
  }catch(e){
    console.log(version + " - COULD NOT REMEMBER THE PAGE: " +
                (e && e.message ? e.message : e));
  }
}

/* ================================================================
CHIPBOOK WITH NO LAPTOP

The end user starts an entry AT THE MACHINE, with the laptop shut: they
type the name, the customer and the notes, take photos or films - and it
waits IN THE PHONE. Once the laptop is switched on, one button moves it
all into the main database.

WHY A SEPARATE SCREEN AND NOT THE WINDOW ADAPTED: the chipbook window
lives off the database - the list, the search, the AI, the preview. With
the laptop shut there is nothing to draw them from. Rather than showing
ten empty places, we show THE ONE thing that makes sense then. The
existing window code stays untouched.

WHAT IS NOT HERE AND THAT IS DELIBERATE: searching, the list of entries
and the AI. All three need the database, and the database is on the
laptop.
================================================================ */

/* The version number WRITTEN INTO THE PAGE by the server, and not fetched
from /api/status: with the laptop shut nothing answers there, and that is
exactly when somebody has to tell "it does not work" from "the fix never
reached the phone".
THE WINDOW BAR CARRIES NO NUMBER. By the logo it was decoration: on the
laptop the number is one request away (/api/status) and the error screen
prints it anyway. On the phone with the laptop shut neither of those holds,
so there it stays by the logo. */
const WINDOW_VERSION = "__WINDOW_VERSION__";

const QUEUE_NAME = "chipbook-kolejka";
let queueDb = null;

function queueOpen(){
  return new Promise((ok, bad) => {
    if (queueDb) return ok(queueDb);
    const z = indexedDB.open(QUEUE_NAME, 1);
    z.onupgradeneeded = () => z.result.createObjectStore(
        "jobs", {keyPath: "id", autoIncrement: true});
    z.onsuccess = () => { queueDb = z.result; ok(queueDb); };
    z.onerror = () => bad(z.error);
  });
}

function queueRun(mode, work){
  return queueOpen().then(b => new Promise((ok, bad) => {
    const t = b.transaction("jobs", mode);
    const outcome = work(t.objectStore("jobs"));
    t.oncomplete = () => ok(outcome && outcome.result);
    t.onerror = () => bad(t.error);
  }));
}

const queueAll = () => queueRun("readonly", s => s.getAll());
const queueAdd = (jobId) => queueRun("readwrite", s => s.add(jobId));
const queueDelete = (id) => queueRun("readwrite", s => s.delete(id));

/* THE PHONE gives the entry its mark and does not change it on a repeated
send - thanks to that a repeat makes no second entry in the database. */
function newMark(){
  if (window.crypto && crypto.randomUUID) return "tel-" + crypto.randomUUID();
  return "tel-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
}

/* File names from a phone are unreadable:
"80789984275__611B902B-1912-4B2F-A086-69AA8E9E9D3C.MOV".
In the database such names are useless when searching. */
function readableName(file, index, formatWhen){
  const dot = (file.name || "").lastIndexOf(".");
  const suffix = dot > 0 ? file.name.slice(dot).toLowerCase() : "";
  const kind = (file.type || "").startsWith("video") ? "video" : "photo";
  return formatWhen.slice(0, 10) + "_" + kind + "-" + index + suffix;
}

async function laptopIsUp(){
  /* WE ASK AT AN /api/ ADDRESS, AND THAT IS THE WHOLE POINT OF THIS FUNCTION.
  CAUGHT IN USE: the first version asked for the app description, that is
  for a thing the phone HAS AT ITS OWN END in the stored copy. It answered
  itself "the laptop is up", went on and hung on a request for the code it
  had no way of meeting. The screen for a shut laptop did not appear at all.
  /api/ addresses NEVER come from the copy (see sw.js), so only they tell
  the truth about whether the laptop really answers.

  EVERY ANSWER MEANS "THE LAPTOP IS UP" - a refusal included. What interests
  us is whether somebody is on the other side, not whether they let us in. */
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), 4000);
  try{
    await fetch("/api/status", {cache: "no-store", signal: control.signal,
                              headers: {"X-Chipbook-Token": TOKEN || ""}});
    return true;
  }catch(e){
    return false;
  }finally{
    clearTimeout(timer);
  }
}

function noLaptopScreen(withLaptop){
  /* THE SAME SCREEN SERVES TWO SITUATIONS (reported).
  With the laptop SHUT the entry waits in the phone.
  With it RUNNING the same form goes straight into the database - the end
  user creates an entry from the phone exactly as at the machine, and adds
  the PDF, the setup sheet and the NC program later on the laptop, because
  those files cannot be pointed at from a phone anyway.
  ONE form instead of two: the same field checks, the same file names, the
  same entry key. A second form would sooner or later drift apart from this
  one. */
  /* IT LOOKS LIKE CHIPBOOK, NOT LIKE ANOTHER PROGRAM.
  We CLONE the header from the real window rather than writing it a second
  time - thanks to that it cannot accidentally drift apart from the
  original. The search, the AI switch and "All jobs" are DIMMED: all three
  live off the database, which stands on the laptop. They do not vanish, so
  that the user does not think something has broken. */
  const el = document.createElement("div");
  el.id = "offline-screen";

  /* THE HEADER WRITTEN HERE, AND NOT CLONED FROM THE WINDOW.
  CAUGHT ON A PHONE: the clone brought with it a layout prepared for a wide
  screen, and on a narrow one the fields overlapped. The window has a layout
  of its own for phones, which the clone was not getting. Here we draw it
  plainly: the logo, and under it a dimmed search. */
  const top = document.createElement("header");
  top.className = "off-bar";
  top.innerHTML =
    '<div class="off-brand"><span class="off-logo"></span>chipbook' +
      '<span class="version-small">v' + WINDOW_VERSION + '</span></div>' +
    '<div class="off-dimmed">' +
      '<div class="off-search">Search by material, tool...</div>' +
      '<div class="off-modes"><span class="off-mode-chosen">By hand</span>' +
      '<span>Ask the AI</span></div>' +
    '</div>';
  el.appendChild(top);

  const rest = document.createElement("div");
  rest.innerHTML =
    (withLaptop
      ? '<div class="off-alert">The entry goes into the database at once. ' +
        'Files other than photos and films are added on the laptop.</div>'
      : '<div class="off-alert">The laptop is switched off. The entry ' +
        'stays in the phone and goes into the database when you switch ' +
        'the laptop on.</div>') +
    '<div class="off-content">' +
      '<div class="off-section"><b>New entry</b><i id="off-counter"></i></div>' +
      '<label for="off-name">Name <span class="off-star">*</span></label>' +
      '<input type="text" id="off-name" autocomplete="off">' +
      '<label for="off-customer">Customer <span class="off-star">*</span></label>' +
      '<input type="text" id="off-customer" autocomplete="off">' +
      '<label for="off-material">Material ' +
        '<span class="off-star">*</span></label>' +
      '<input type="text" id="off-material" autocomplete="off">' +
      '<label for="off-order_number">Order number</label>' +
      '<input type="text" id="off-order_number" autocomplete="off">' +
      '<label for="off-notes">Notes</label>' +
      '<textarea id="off-notes"></textarea>' +
      '<div class="off-section"><b>Photos and films</b>' +
        '<i id="off-file-count"></i></div>' +
      '<label class="off-button" for="off-files">Add a photo or a video' +
        /* NO `accept` - for the same reason as in dropZoneHtml and against what
        stood here before. `accept="image/*,video/*"` looks like a narrowing to
        photos and films, and on Android it TAKES the camera away: Chrome then
        opens Google Photos, that is the gallery alone. An empty field gives the
        camera, video and files at once.
        This is the screen AT THE MACHINE, so the camera matters here more than
        anything else (measured). */
        '<input id="off-files" type="file" multiple>' +
      '</label>' +
      '<div class="off-small" id="off-chosen"></div>' +
      '<button type="button" class="off-button off-accent" id="off-save">' +
        'Save in the phone</button>' +
      '<div class="off-small" id="off-log"></div>' +
      '<div id="off-queue-block" hidden>' +
        '<div class="off-section"><b>Waiting in the phone</b>' +
          '<i id="off-waiting-count"></i></div>' +
        '<div id="off-queue"></div>' +
        '<button type="button" class="off-button off-accent" id="off-send">' +
          'Send to the laptop</button>' +
      '</div>' +
      '<div class="off-section off-dimmed"><b>All jobs</b>' +
        '<i>they come back when the laptop is switched on</i></div>' +
    '</div>';
  while (rest.firstChild) el.appendChild(rest.firstChild);
  document.body.appendChild(el);

  let chosenFiles = [];
  let previews = [];        /* preview URLs - to be revoked */
  const say = (msg) => {
    document.getElementById("off-log").textContent = msg || "";
  };

  /* EVERY PHOTO HAS ITS OWN CROSS.
  We number photos and films separately and in the same way readableName
  will at saving - so that what is seen on screen matches what afterwards
  lies in the entry folder.
  WE RELEASE THE PREVIEWS AT EVERY DRAW. A photo from a phone weighs several
  MB; without this twenty additions and deletions in a day would hold
  everything ever chosen in the phone memory. */
  function showChosen(){
    for (const address of previews) URL.revokeObjectURL(address);
    previews = [];
    const list = document.getElementById("off-chosen");
    document.getElementById("off-file-count").textContent =
      chosenFiles.length ? ("added: " + chosenFiles.length) : "";
    if (!list) return;
    if (!chosenFiles.length){ list.innerHTML = ""; return; }
    let photos = 0, videos = 0;
    list.innerHTML = chosenFiles.map((file, i) => {
      const video = (file.type || "").startsWith("video");
      const index = video ? ++videos : ++photos;
      let preview = '<span class="off-file-badge">video</span>';
      if (!video){
        const address = URL.createObjectURL(file);
        previews.push(address);
        preview = '<img class="off-file-preview" src="' + address + '" alt="">';
      }
      return '<div class="off-file-row">' + preview +
        '<span class="off-file-desc"><b>' +
        (video ? "video " : "photo ") + index + '</b>' +
        '<i>' + esc(humanSize(file.size)) + '</i></span>' +
        '<button type="button" class="off-delete-file" data-i="' + i +
        '" aria-label="Remove this photo">\u00d7</button></div>';
    }).join("");
    list.querySelectorAll(".off-delete-file").forEach(button => {
      button.addEventListener("click", () => {
        /* With no "are you sure". This is a file that has gone nowhere yet - and
        a photo is taken again with one touch. The question would cost more than
        it protects. */
        chosenFiles.splice(Number(button.dataset.i), 1);
        showChosen();
      });
    });
  }

  document.getElementById("off-files").addEventListener("change", (ev) => {
    /* WE ADD, we do not replace - caught on a trial. */
    for (const p of ev.target.files) chosenFiles.push(p);
    ev.target.value = "";
    showChosen();
  });

  document.getElementById("off-save").addEventListener("click", async () => {
    const readField = (id) => document.getElementById(id).value.trim();
    const name = readField("off-name");
    const customer = readField("off-customer");
    const material = readField("off-material");
    if (!name || !customer || !material){
      say("The name, the customer and the material are obligatory.");
      return;
    }
    const formatWhen = new Date().toLocaleString("sv").slice(0, 19);
    try{
      await queueAdd({
        idempotency_key: newMark(), name, customer, material,
        order_number: readField("off-order_number"),
        notes: readField("off-notes"),
        when: formatWhen,
        fileList: chosenFiles.map((p, i) => ({
          name: readableName(p, i + 1, formatWhen), data: p})),
      });
    }catch(e){
      say("COULD NOT SAVE IN THE PHONE: " + e);
      return;
    }
    ["off-name","off-customer","off-material","off-order_number","off-notes"]
      .forEach(id => document.getElementById(id).value = "");
    chosenFiles = [];
    showChosen();     /* clears the list and releases the previews */
    if (withLaptop){
      /* The laptop is up - there is no reason to wait. It goes the same road as
      the queue, so a repeated send makes no twin either. */
      say("Sending to the database...");
      try{
        await sendQueue(say);
        location.reload();
        return;
      }catch(e){
        say("The send failed - the entry waits in the phone.");
      }
    } else {
      say("Saved in the phone.");
    }
    await showQueue();
  });

  document.getElementById("off-send").addEventListener("click", async () => {
    say("Looking for the laptop...");
    if (!(await laptopIsUp())){
      say("The laptop still does not answer. The entries go on waiting in the phone.");
      return;
    }
    say("The laptop answers. Opening chipbook...");
    location.reload();
  });

  showQueue();
}

async function showQueue(){
  const el = document.getElementById("off-queue");
  if (!el) return;
  const list = await queueAll();
  const block = document.getElementById("off-queue-block");
  const counter = document.getElementById("off-counter");
  /* When nothing is waiting, the WHOLE section goes along with the "Send to
  the laptop" button - agreed. */
  if (!list || !list.length){
    block.hidden = true;
    if (counter) counter.textContent = "";
    return;
  }
  block.hidden = false;
  if (counter) counter.textContent = "waiting in the phone: " + list.length;
  document.getElementById("off-waiting-count").textContent =
    list.length + (list.length === 1 ? " entry" : " entries");
  el.innerHTML = list.map(w =>
    '<div class="off-job"><b>' + esc(w.name || "(no name)") + '</b>' +
    (w.notes ? '<p>' + esc(w.notes) + '</p>' : "") +
    '<div class="off-small">' + esc(w.customer || "-") + ' \u00b7 ' +
    esc(w.material || "") +
    (w.order_number ? ' \u00b7 ' + esc(w.order_number) : "") +
    ' \u00b7 files ' + w.fileList.length + ' \u00b7 ' + esc(w.formatWhen || "") +
    '</div></div>').join("");
}

async function sendQueue(say){
  say = say || function(){};
  const list = await queueAll();
  if (!list || !list.length) return 0;
  let wentOk = 0;
  for (const w of list){
    try{
      say("Sending \"" + w.name + "\"...");
      const response = await api("/api/jobs/offline", {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: w.idempotency_key, name: w.name, customer: w.customer,
          material: w.material, order_number: w.order_number || "",
          notes: w.notes, when: w.formatWhen}),
      });
      for (const p of w.fileList){
        say("Sending " + p.name + "...");
        /* The file gets a READABLE name. sendFile takes it from the file itself,
        so we assemble it afresh with that name. */
        const withName = new File([p.data], p.name,
                                {type: p.data.type || ""});
        await sendFile(response.id, withName);
      }
      await queueDelete(w.id);
      wentOk += 1;
    }catch(e){
      say("Could not send \"" + w.name + "\": " + e.message +
              " - the entry stays in the phone.");
      break;   /* we do not try further, so as not to multiply the same error */
    }
  }
  return wentOk;
}

/* EVERY ERROR AT STARTUP IS TO REACH THE SCREEN AND NOT STAY A WHITE
PATCH. REPORTED: with the laptop shut the phone showed A WHITE SCREEN -
that is, the page came from the stored copy but the code died before it
drew anything. Without this frame there is no naming that other than
guesswork, and it costs the person at the phone, not me. */
function errorScreen(error){
  const description = (error && error.stack) ? error.stack
             : (error && error.message) ? error.message : String(error);
  const el = document.createElement("div");
  el.id = "startup-error";
  el.innerHTML = "<h2>chipbook did not start</h2><p>window version " +
    WINDOW_VERSION + "</p><pre></pre>" +
    "<p>Send this text on - it says what happened.</p>";
  el.querySelector("pre").textContent = description;
  document.body.appendChild(el);
}

window.addEventListener("error", (ev) => {
  try{ errorScreen(ev.error || ev.message); }catch(e){}
});
window.addEventListener("unhandledrejection", (ev) => {
  try{ errorScreen(ev.reason); }catch(e){}
});

function newJobButtonOnPhone(){
  /* On the laptop such a button is already in the header. On the phone it
  was not there, because until then the phone could not create entries at
  all. */
  if (location.hostname === "127.0.0.1" || location.hostname === "localhost")
    return;
  const g = document.createElement("button");
  g.id = "off-new-on-phone";
  g.textContent = "+ New entry";

  /* ONE BUTTON, TWO ROLES (reported).
  With the form open, "New entry" makes no sense - it is already open - and
  what is missing is a road back. The same button turns into an arrow then
  and calls "Cancel", that is the same road as on the laptop. No second
  button to keep up.
  We ask every half second, because the form draws the code and we do not
  want to touch that while doing this - one topic at a time. */
  /* WITH THE FORM OPEN THE BUTTON GOES (reported).
  Earlier it turned into "Back" and overlapped "Save entry". The road back
  is already in the form - it is "Cancel". One road instead of two. */
  let formWasThere = null;
  setInterval(() => {
    const isOpen = !!document.getElementById("btn-cancel");
    if (isOpen === formWasThere) return;
    formWasThere = isOpen;
    g.hidden = isOpen;
  }, 500);

  /* THE BUTTON TAKES ITSELF OUT OF THE WAY (reported from a photo of a phone
  screen). Three situations, but one cause: on a phone that corner of the
  screen is sometimes needed for something more important.
  - YOU ARE TYPING (the AI field or a manual search) - the keyboard makes
    the screen half as short and the button lands on "Send";
  - YOU ARE BROWSING - while scrolling it covers the rows of the list;
  - THE AI WINDOW IS OPEN - see the rule in the styles above.
  All three are settled by two markers on <body> and the styles; the button
  itself knows nothing about it.
  ON THE LAPTOP THERE IS NONE OF THIS - the function does not run there. */
  function refreshTyping(){
    const a = document.activeElement;
    const typing = !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA");
    document.body.dataset.typing = typing ? "1" : "";
  }
  document.addEventListener("focusin", refreshTyping);
  /* When jumping between fields focusout comes BEFORE focusin - without this
  wait the button would blink at every change of field. */
  document.addEventListener("focusout", () => setTimeout(refreshTyping, 0));

  let moveEnd = null;
  document.addEventListener("scroll", () => {
    document.body.dataset.scrolling = "1";
    clearTimeout(moveEnd);
    moveEnd = setTimeout(() => {
      document.body.dataset.scrolling = "";
    }, 500);
  }, {capture: true, passive: true});

  g.addEventListener("click", () => {
    /* WE CALL THE SAME BUTTON AS ON THE LAPTOP, and not a screen of our own.
    REPORTED: with the laptop running the form is to be the ORDINARY one - the
    same, the same fields, the same save. My emergency screen serves the
    situation where the database is not there, and nothing else. */
    const real = document.getElementById("btn-new");
    if (real) real.click();
  });
  document.body.appendChild(g);
}

(async function start(){
 try{
  /* We do NOT wait for the remembering - it is to happen alongside rather
  than delay the window opening. The result will show in the corner when it
  is known. */
  rememberInPhone();
  if (!(await laptopIsUp())){
    noLaptopScreen();
    return;
  }
  if (!(await passGate())) return;
  await run();
  newJobButtonOnPhone();
  /* Entries made with the laptop shut come in BY THEMSELVES once the window
  has come up normally - the user is to remember nothing. */
  try{
    const waiting = await queueAll();
    if (waiting && waiting.length){
      await sendQueue();
      await refreshList();
    }
  }catch(e){ /* a missing queue is no reason to break the window */ }
 }catch(error){
  errorScreen(error);
 }
})();
