import test from 'node:test';
import assert from 'node:assert/strict';
import {ASSETS,FRAMES,collectAsset,normalize} from './live_worker.mjs';
const NOW=1789989870000;
function api(url){
 const u=new URL(url),q=u.searchParams,symbol=q.get('symbol'),asset=symbol.slice(0,-4),bb=u.host.includes('bybit');
 if(u.pathname.includes('exchangeInfo'))return {symbols:[{symbol,baseAsset:asset,quoteAsset:'USDT',status:'TRADING',isSpotTradingAllowed:true}]};
 if(u.pathname.includes('instruments-info'))return {retCode:0,result:{category:'spot',list:[{symbol,baseCoin:asset,quoteCoin:'USDT',status:'Trading'}]}};
 const i=bb?Object.keys(FRAMES).find(k=>FRAMES[k][2]===q.get('interval')):q.get('interval');
 const [period,count]=FRAMES[i],cutoff=Number(q.get(bb?'end':'endTime'));
 const rows=Array.from({length:count},(_,n)=>{const t=(Math.floor(cutoff/period)-count+1+n)*period,p=bb?'202':'102';return bb?[String(t),p,'205','99',p,'50','10000']:[t,p,'205','99',p,'50',t+period-1,'10000',10,'20','4000'];});
 return bb?{retCode:0,result:{category:'spot',symbol,list:rows.reverse()}}:rows;
}
const fetcher=async u=>Response.json(api(u));
test('six assets and all frames use primary',async()=>{for(const a of ASSETS){const p=await collectAsset(a,{fetcher,clock:()=>NOW});assert.equal(p.exchange,'Binance');assert.equal(Object.keys(p.frames).length,6);}});
test('partial primary failure discards all primary frames; Bybit nulls',async()=>{
 const p=await collectAsset('BTC',{clock:()=>NOW,fetcher:async u=>{if(u.includes('binance')&&u.includes('interval=1h&'))return new Response('',{status:503});return fetcher(u);}});
 assert.equal(p.exchange,'Bybit');for(const f of Object.values(p.frames)){assert.equal(f.exchange,'Bybit');assert.ok(f.candles.every(c=>c.close===202&&c.taker_buy_base===null&&c.trades===null));}
});
test('both exchanges down fail closed',async()=>{const p=await collectAsset('BTC',{clock:()=>NOW,fetcher:async()=>new Response('',{status:503})});assert.equal(p.status,'ERROR');assert.deepEqual(p.frames,{});});
test('Bybit failure does not affect primary',async()=>{const p=await collectAsset('BTC',{clock:()=>NOW,fetcher:async u=>u.includes('bybit')?new Response('',{status:503}):fetcher(u)});assert.equal(p.exchange,'Binance');});
test('missing pair blocks klines',async()=>{const p=await collectAsset('BNB',{exchange:'Bybit',clock:()=>NOW,fetcher:async u=>{assert.ok(u.includes('instruments-info'));return Response.json({retCode:0,result:{category:'spot',list:[]}});}});assert.equal(p.attempts[0].status,'SYMBOL_UNAVAILABLE');});
test('Bybit reverse order, duplicate and stale checks',()=>{
 const url='https://api.bybit.com/v5/market/kline?symbol=BTCUSDT&interval=60&limit=240&end='+NOW;
 const raw=api(url).result.list,bars=normalize(raw,'Bybit','1h',NOW);assert.ok(bars[0].open_ms<bars.at(-1).open_ms);
 const duplicate=structuredClone(raw);duplicate[0]=duplicate[1];assert.throws(()=>normalize(duplicate,'Bybit','1h',NOW),/Duplicate/);
 const stale=raw.map(r=>[String(Number(r[0])-864000000),...r.slice(1)]);assert.throws(()=>normalize(stale,'Bybit','1h',NOW),/stale/);
});
test('hour closure uses observation cutoff and one-second grace',async()=>{const boundary=Math.floor(NOW/3600000)*3600000;const p=await collectAsset('BTC',{fetcher,clock:()=>boundary+1000});const bars=p.frames['1h'].candles;assert.equal(bars.at(-1).complete,false);assert.equal(bars.at(-2).complete,true);});
