// Independent, on-demand Spot feed. No GitHub reads and no trading operations.
export const ASSETS = ['BTC','ETH','SOL','XRP','DOGE','BNB'];
export const FRAMES = {'1m':[60000,120,'1'],'5m':[300000,160,'5'],'15m':[900000,160,'15'],'1h':[3600000,240,'60'],'4h':[14400000,120,'240'],'1d':[86400000,80,'D']};
const BASES = {Binance:'https://data-api.binance.vision',Bybit:'https://api.bybit.com'};
const FIELDS=['open_ms','open_utc','close_ms','open','high','low','close','volume','quote_volume','complete'];
const iso = n => new Date(n).toISOString();
class FeedError extends Error { constructor(status,message){super(message);this.status=status;} }
async function request(url, fetcher){
  const r=await fetcher(url,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(8000)});
  if(!r.ok)throw new FeedError('ERROR',`HTTP ${r.status} from ${new URL(url).origin}`);
  return await r.json();
}
function bybit(p){
  if(p?.retCode!==0||p?.result?.category!=='spot')throw new FeedError('ERROR',`Bybit API ${p?.retCode}: ${p?.retMsg}`);
  return p.result;
}
export function normalize(rows,exchange,interval,cutoff){
  const [period,count]=FRAMES[interval];
  if(!Array.isArray(rows)||rows.length<count)throw new FeedError('ERROR','Insufficient candle history');
  const bars=rows.map(r=>{
    if(!Array.isArray(r)||r.length<(exchange==='Binance'?11:7))throw new FeedError('ERROR','Malformed candle');
    const t=Number(r[0]), closeMs=exchange==='Binance'?Number(r[6]):t+period-1;
    const [o,h,l,c,v]=r.slice(1,6).map(Number),q=Number(r[exchange==='Binance'?7:6]);
    const taker=exchange==='Binance'?Number(r[9]):null,trades=exchange==='Binance'?Number(r[8]):null;
    if(![o,h,l,c,v,q,...(taker===null?[]:[taker])].every(x=>Number.isFinite(x)&&x>=0)||l<=0||l>Math.min(o,c)||h<Math.max(o,c))throw new FeedError('ERROR','Invalid OHLCV');
    if(!Number.isSafeInteger(t)||t%period||closeMs!==t+period-1||t>cutoff)throw new FeedError('ERROR','Invalid time boundaries');
    if(trades!==null&&(!Number.isInteger(trades)||trades<0||taker>v+1e-8))throw new FeedError('ERROR','Invalid trade fields');
    return {open_ms:t,open_utc:iso(t),close_ms:closeMs,open:o,high:h,low:l,close:c,volume:v,quote_volume:q,taker_buy_base:taker,trades,complete:closeMs<cutoff-1000};
  }).sort((a,b)=>a.open_ms-b.open_ms);
  if(bars.some((b,i)=>i&&b.open_ms-bars[i-1].open_ms!==period))throw new FeedError('ERROR','Duplicate candle or gap');
  if(bars.at(-1).open_ms<Math.floor((cutoff-5000)/period)*period)throw new FeedError('STALE','Latest candle is stale');
  const closed=bars.filter(b=>b.complete);
  if(!closed.length)throw new FeedError('ERROR','No closed candles');
  if(closed.at(-1).close_ms+1<Math.floor((cutoff-1000)/period)*period)throw new FeedError('STALE','Last closed candle is stale');
  return bars;
}
export async function collectAsset(asset, {fetcher=fetch,clock=Date.now,exchange=null}={}){
  if(!ASSETS.includes(asset)||![null,'Binance','Bybit'].includes(exchange))throw new Error('Unsupported asset or exchange');
  const attempts=[];
  for(const name of exchange?[exchange]:['Binance','Bybit']){
    const host=BASES[name],symbol=asset+'USDT',started=clock();
    try {
      const pairUrl=host+(name==='Binance'?'/api/v3/exchangeInfo?symbol=':'/v5/market/instruments-info?category=spot&symbol=')+symbol;
      const pairData=await request(pairUrl,fetcher);
      const pairs=name==='Binance'?pairData.symbols:bybit(pairData).list;
      if(!Array.isArray(pairs)||!pairs.some(p=>p.symbol===symbol&&(name==='Binance'?p.status==='TRADING'&&p.baseAsset===asset&&p.quoteAsset==='USDT'&&p.isSpotTradingAllowed===true:p.status==='Trading'&&p.baseCoin===asset&&p.quoteCoin==='USDT')))throw new FeedError('SYMBOL_UNAVAILABLE','Active Spot pair not found');
      const entries=await Promise.all(Object.entries(FRAMES).map(async([interval,[period,limit,byInterval]])=>{
        const cutoff=clock(),params=new URLSearchParams({symbol,limit:String(limit)});
        if(name==='Binance'){params.set('interval',interval);params.set('endTime',String(cutoff));}
        else{params.set('category','spot');params.set('interval',byInterval);params.set('end',String(cutoff));}
        const url=host+(name==='Binance'?'/api/v3/klines?':'/v5/market/kline?')+params;
        const raw=await request(url,fetcher),result=name==='Binance'?raw:bybit(raw);
        if(name==='Bybit'&&result.symbol!==symbol)throw new FeedError('ERROR','Wrong symbol');
        const candles=normalize(name==='Binance'?result:result.list,name,interval,cutoff),closed=candles.filter(c=>c.complete);
        return [interval,{status:'OK',exchange:name,market:name+' Spot',symbol,interval,source:url,source_url:url,
          observation_started_at_utc:iso(cutoff),captured_at_utc:iso(clock()),expires_at_utc:iso(cutoff+120000),
          available_fields:[...FIELDS,...(name==='Binance'?['taker_buy_base','trades']:[])],unavailable_fields:name==='Binance'?[]:['taker_buy_base','trades'],
          last_completed_close_utc:iso(closed.at(-1).close_ms+1),closed_candles:closed.length,candles}];
      }));
      if(clock()-started>=120000)throw new FeedError('STALE','Collection exceeded freshness limit');
      return {schema_version:2,asset,status:'OK',exchange:name,market:name+' Spot',source_url:host,pair_source_url:pairUrl,
        capture_time_utc:iso(clock()),collection_started_at_utc:iso(started),expires_at_utc:iso(started+120000),
        delivery:'independent-on-demand',github_required:false,available_fields:entries[0][1].available_fields,
        unavailable_fields:entries[0][1].unavailable_fields,attempts,frames:Object.fromEntries(entries)};
    }catch(e){attempts.push({exchange:name,source_url:host,status:e.status||'ERROR',error:String(e.message).slice(0,240)});}
  }
  const captured=iso(clock());
  return {schema_version:2,asset,status:attempts.some(a=>a.status==='STALE')?'STALE':'ERROR',exchange:null,source_url:null,
    capture_time_utc:captured,expires_at_utc:captured,delivery:'independent-on-demand',github_required:false,
    available_fields:[],unavailable_fields:[...FIELDS,'taker_buy_base','trades'],attempts,frames:{}};
}
function response(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','Access-Control-Allow-Origin':'*','X-Content-Type-Options':'nosniff'}});}
export default {async fetch(req){
  const u=new URL(req.url);
  if(req.method!=='GET')return response({error:'GET only'},405);
  const match=u.pathname.match(/^\/api\/(BTC|ETH|SOL|XRP|DOGE|BNB)\.json$/);
  if(match){
    const exchange=u.searchParams.get('exchange');
    if(exchange&&!['Binance','Bybit'].includes(exchange))return response({error:'Invalid exchange'},400);
    const data=await collectAsset(match[1],{exchange});
    return response(data,data.status==='OK'?200:503);
  }
  if(u.pathname==='/manifest.json')return response({schema_version:2,delivery:'independent-on-demand',github_required:false,
    snapshot_max_age_seconds:120,priority:['Binance','Bybit'],assets:Object.fromEntries(ASSETS.map(a=>[a,new URL('/api/'+a+'.json',u).href])),intervals:Object.keys(FRAMES)});
  if(u.pathname!=='/')return response({error:'Not found'},404);
  return new Response('<!doctype html><meta charset="utf-8"><title>Adaptive 1H Live Feed</title><h1>Adaptive 1H Live Feed</h1><p>Fresh public Spot candles fetched on demand. Binance first, Bybit fallback. No GitHub dependency. Check status and expires_at_utc before use.</p><p><a href="/manifest.json">JSON manifest</a></p><ul>'+ASSETS.map(a=>'<li><a href="/api/'+a+'.json">'+a+' live JSON</a> — <a href="/api/'+a+'.json?exchange=Bybit">Bybit verification</a></li>').join('')+'</ul><p>Intervals: 1m, 5m, 15m, 1h, 4h, 1d. One exchange per asset; unavailable trade fields are null. No trading signals or orders.</p>',{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
}};
