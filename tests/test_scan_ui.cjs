const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = Object.fromEntries(['bannerScan','bannerText','scanDetail','scanProgress','btnScan'].map(id => [id, {style:{}, textContent:'', disabled:false}]));
const timers = new Map();
let timerId = 0;
let response;
let requests = 0;
const sandbox = {
  window: {addEventListener(){}}, document: {getElementById: id => nodes[id] || null},
  console, AbortSignal,
  setTimeout: f => {timers.set(++timerId, f); return timerId;},
  clearTimeout: id => timers.delete(id),
  fetch: async () => {requests++; if (response instanceof Error) throw response; return {ok:true, json:async()=>response};}
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/js/app.js','utf8'),sandbox);
vm.runInContext('fetchStats = () => {}; loadOpportunities = () => {}; showToast = () => {};',sandbox);
(async () => {
  response = {is_scanning:true, progress:{queries_done:3,queries_total:45,pages_done:4,elapsed_seconds:12}};
  await Promise.all([sandbox.pollScanStatus(),sandbox.pollScanStatus()]);
  assert.equal(requests,1);
  assert.equal(nodes.btnScan.disabled,true);
  assert.match(nodes.scanDetail.textContent,/3\/45/);
  assert.equal(timers.size,1);
  response = {is_scanning:false,progress:{},last_scan_result:{status:'PARTIAL',limite_tiempo:true}};
  await sandbox.pollScanStatus();
  assert.equal(nodes.btnScan.disabled,false);
  assert.equal(nodes.bannerText.textContent,'Escaneo parcial');
  assert.equal(timers.size,0);
  response = new Error('offline');
  await sandbox.pollScanStatus();
  assert.match(nodes.bannerText.textContent,/Sin conexión/);
  assert.equal(timers.size,1);
  response = {is_scanning:false,progress:{},last_scan_result:{status:'ERROR',error:'OECE failed'}};
  await sandbox.pollScanStatus();
  assert.equal(nodes.bannerText.textContent,'El escaneo falló');
  assert.equal(nodes.scanDetail.textContent,'OECE failed');
  console.log('UI OK: single polling, resume, partial, network retry, error.');
})().catch(error => {console.error(error); process.exitCode=1;});
