// stand_bridge.js — НАСТОЯЩИЙ код моста (Bridge.js + BotData.js из снятой копии) в node vm.
// Подменены только платформенные службы Apps Script: лист очереди — массив в памяти,
// LockService — пустой замок, ContentService — строка, verifyToken — «да» (токена у стенда нет).
// Роутинг doPost/doGet, enqueueTask_, getPending_ — дословно из копии моста.
// Протокол: одна JSON-строка на входе ({"method","contents"|"parameter"}) → одна на выходе ({"content"}).
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const readline = require('readline');

const SRC = process.argv[2];
if (!SRC) { console.error('usage: node stand_bridge.js <каталог копии моста>'); process.exit(2); }

const sheet = [];   // строка 1 — заголовки, дальше ряды
const tab = {
  getLastRow: () => sheet.length,
  appendRow: (arr) => { sheet.push(arr.slice()); },
  getRange: (row, col, numRows, numCols) => {
    numRows = numRows || 1; numCols = numCols || 1;
    return {
      getValues: () => {
        const out = [];
        for (let r = 0; r < numRows; r++) {
          const src = sheet[row - 1 + r] || [];
          const line = [];
          for (let c = 0; c < numCols; c++) line.push(src[col - 1 + c] === undefined ? '' : src[col - 1 + c]);
          out.push(line);
        }
        return out;
      },
      getValue: () => { const src = sheet[row - 1] || []; return src[col - 1] === undefined ? '' : src[col - 1]; },
      setValue: (v) => { sheet[row - 1] = sheet[row - 1] || []; sheet[row - 1][col - 1] = v; },
    };
  },
};

const ctx = {
  console: { log() {}, error() {}, warn() {} },
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  ContentService: {
    MimeType: { JSON: 'application/json' },
    createTextOutput: (s) => ({ _s: s, setMimeType() { return this; }, getContent() { return this._s; } }),
  },
};
vm.createContext(ctx);
for (const f of ['Bridge.js', 'BotData.js']) {
  vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), ctx, { filename: f });
}
vm.runInContext('verifyToken = function () { return true; };', ctx);
ctx.__tab = tab;
vm.runInContext('getBotTab_ = function () { return __tab; };', ctx);
sheet.push(vm.runInContext('BOTDATA.QUEUE_HEADERS.slice()', ctx));

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  let out;
  try {
    const req = JSON.parse(line);
    const e = req.method === 'POST' ? { postData: { contents: req.contents } } : { parameter: req.parameter };
    ctx.__e = e;
    const res = vm.runInContext(req.method === 'POST' ? 'doPost(__e)' : 'doGet(__e)', ctx);
    out = { content: res.getContent() };
  } catch (err) {
    out = { error: String(err && err.stack || err) };
  }
  process.stdout.write(JSON.stringify(out) + '\n');
});
