"use strict";
let handoffEpoch=0, handoffTurn=null, handoffSelected=null;
function resetHandoff(){handoffEpoch++;handoffTurn=null;handoffSelected=null;}
const deliveryLabels={prepared:"전송 전 · 확인 대기",sending:"전송 시도 중 · 중복 전송 차단",sent:"Telegram API 접수 · 열람 미확인",unknown:"결과 미확인 · 수신처 확인 필요",queued:"에이전트 수신함 대기",claimed:"에이전트 접수",replied:"에이전트 회신 도착",simulated:"모의 전송 · 실제 전송 없음"};
function handoffButton(label,handler){
  const b=el("button",label); b.type="button";
  b.addEventListener("click",async()=>{
    b.disabled=true; $("#handoffError").hidden=true;
    try{await handler();}catch(e){$("#handoffError").textContent=e.message;$("#handoffError").hidden=false;}finally{b.disabled=false;}
  }); return b;
}
async function openHandoff(turn=null){
  handoffTurn=turn;handoffSelected=null;
  if(!$("#handoffPanel").open)$("#handoffPanel").showModal();
  await renderHandoff();
}
async function renderHandoff(){
  const epoch=++handoffEpoch, data=await api("/api/handoff/state"+(handoffTurn?"?chat_id="+encodeURIComponent(handoffTurn.chat_id):""));
  if(epoch!==handoffEpoch||!$("#handoffPanel").open)return;
  const root=$("#handoffContent");root.replaceChildren();
  root.append(el("p",(data.mode==="internal"?"내부망":"외부망")+" 문서함 · 최근 50개 문서. 원본과 수정본, 전달 결과와 회신을 별도로 보존합니다."),handoffButton("수신 상태 새로고침",renderHandoff));
  const reports=data.reports.filter(r=>!handoffTurn||r.turn_id===handoffTurn.id);
  if(handoffTurn)root.append(handoffButton("답변으로 새 문서 작성",()=>reportEditor(root,data,null)));
  if(!reports.length)root.append(el("p","저장된 문서가 없습니다. 답변 아래의 ‘문서화 · 에이전트 전달’에서 시작하세요."));
  for(const report of reports){
    const row=el("div",undefined,"source");
    row.append(handoffButton(report.title+" · "+new Date(report.created).toLocaleString("ko-KR")+(report.parent_id?" · 수정본":" · 최초 문서"),()=>{handoffSelected=report.id;return renderHandoff();}));root.append(row);
  }
  const selectedReport=reports.find(r=>r.id===handoffSelected)||reports[0];if(!selectedReport)return;
  const report=await api("/api/report/"+encodeURIComponent(selectedReport.id));
  if(epoch!==handoffEpoch||!$("#handoffPanel").open)return;
  handoffSelected=report.id;
  const section=el("section",undefined,"handoff-document");
  section.append(el("h3",report.title),el("p","문서 ID "+report.id+" · 내용 지문 "+report.sha256.slice(0,16)),el("pre",report.text),
    handoffButton("Markdown 문서 다운로드",()=>download("AlphaDrex-"+report.id+".md",report.text,"text/markdown;charset=utf-8")),handoffButton("수정본 별도 작성",()=>reportEditor(root,data,report)));
  const review=data.reviews.find(r=>r.report_id===report.id&&r.sha256===report.sha256);
  section.append(el("h3","전달 내용 검토"));
  if(review)section.append(el("p",review.reviewer+" · "+review.note+(review.reviewer_verified?" · 로그인 계정으로 확인된 검토 기록이며 업무 승인은 아닙니다.":" · 전달 내용 검토 기록이며 신원 인증·업무 승인이 아닙니다.")));
  else{
    const [rw,ri]=field(state.accounts_enabled?"검토자 (로그인 계정으로 자동 기록)":"검토자 표시 이름 (계정 없음 · 본인 확인 안 됨)");
    if(state.accounts_enabled){ri.value=state.user.name;ri.readOnly=true;}
    const [nw,ni]=field("검토 메모"),[cw,ci]=checkbox("확인한 사실·실제 조치·미확인 사항과 근거를 구분해 검토했습니다");
    section.append(rw,nw,cw,handoffButton("이 문서 버전의 검토 저장",async()=>{
      if(!ci.checked)throw Error("문서 내용을 검토하고 확인란을 선택하세요.");
      await api("/api/report/review",{mode:data.mode,report_id:report.id,sha256:report.sha256,confirmed:true,reviewer:ri.value,note:ni.value});await renderHandoff();
    }));
  }
  section.append(el("h3","담당 에이전트에 전달"));
  const ordered=[...data.targets].sort((a,b)=>(a.transport==="agent"?0:1)-(b.transport==="agent"?0:1));
  // Default to a target that can actually receive it: a connected agent, else the simulation (review A-3).
  const ready=ordered.find(t=>t.transport==="agent"&&t.configured)||ordered.find(t=>t.id==="simulation")||ordered[0];
  const [tw,ti]=select("수신 대상",ordered.map(t=>[t.id,t.label+(t.configured?"":" · 연결 안 됨(전달 불가)")]),ready?.id);
  for(const o of ti.options){const t=ordered.find(x=>x.id===o.value);if(t&&t.transport==="agent"&&!t.configured)o.disabled=true;}
  section.append(tw,el("p","수신함 등록은 접수·분석·업무 완료와 다릅니다. 에이전트가 연결되면 접수 상태와 회신을 확인할 수 있습니다."));
  const preview=el("div");
  ti.addEventListener("change",()=>preview.replaceChildren());
  const prepare=handoffButton("전달 문서와 수신자 미리보기",async()=>{
    const delivery=await api("/api/handoff/prepare",{mode:data.mode,report_id:report.id,target_id:ti.value});
    preview.replaceChildren(el("h3","최종 전달 확인"),el("p",delivery.target.label+(delivery.target.chat_id?" · "+delivery.target.chat_id:"")),el("pre",report.text),el("p","상태: "+(deliveryLabels[delivery.status]||delivery.status)));
    if(delivery.status!=="prepared"){preview.append(el("p","이미 처리된 동일 문서·수신 대상 요청입니다. 중복 발송하지 않습니다."));return;}
    const [cw,ci]=checkbox("위 수신자에게 표시된 문서 전체를 전달합니다");preview.append(cw);let external;
    if(delivery.target.transport==="telegram"){const [ew,ei]=checkbox("공개·가상 자료임을 확인하고 Telegram 외부 전송에 동의합니다");external=ei;preview.append(ew);}
    preview.append(handoffButton(delivery.target.transport==="agent"?"확인 후 에이전트 수신함 등록":delivery.target.transport==="simulation"?"모의 전송 실행":"확인 후 Telegram 실제 전송",async()=>{
      if(!ci.checked||(external&&!external.checked))throw Error("수신자·문서와 전송 확인란을 확인하세요.");
      await api("/api/handoff/send",{mode:data.mode,delivery_id:delivery.id,confirmation:delivery.confirmation,confirmed:true,external_confirmed:!!external?.checked});await renderHandoff();
    }));
  });prepare.disabled=!review;section.append(prepare,preview);
  for(const delivery of data.deliveries.filter(d=>d.report_id===report.id)){
    const card=el("div",undefined,"source");card.append(el("strong",delivery.target.label+" · "+(deliveryLabels[delivery.status]||delivery.status)),el("p",delivery.notice||""));
    for(const receipt of data.receipts.filter(r=>r.delivery_id===delivery.id))card.append(el("h4","수신 에이전트 회신 · 담당자 검토 전"),el("pre",receipt.text),handoffButton("회신으로 새 검토 문서 작성",()=>reportEditor(root,data,{...report,source_kind:"receipt",source_id:receipt.id,content:receipt.text})));
    section.append(card);
  }root.append(section);
}
function reportEditor(root,data,parent){
  root.querySelector(".handoff-editor")?.remove();const turn=handoffTurn;
  if(!parent&&!turn)throw Error("문서로 만들 답변을 선택하세요.");
  const editor=el("section",undefined,"handoff-editor");editor.append(el("h3",parent?"수정본 작성 · 기존 버전 유지":"답변을 업무 문서로 저장"));
  let sourceKind=parent?.source_kind||"turn",sourceId=parent?.source_id||turn.id;
  const [titleW,title]=field("문서 제목","text",parent?.title||"현장 업무 검토 요청서");title.maxLength=120;
  const [factW,facts]=field("작성자가 확인한 사실","textarea",parent?.facts||"");facts.maxLength=4000;
  const [actionW,actions]=field("실제 수행한 조치 (권고와 구분)","textarea",parent?.actions||"");actions.maxLength=4000;
  const [requestW,request]=field("수신 에이전트에게 요청할 사항","textarea",parent?.request||"내용 검토와 후속 확인 요청");request.maxLength=2000;
  const [bodyW,body]=field("검토 내용","textarea",parent?.content||turn?.draft?.text||turn?.template||"");body.rows=12;body.maxLength=24000;editor.append(titleW);
  if(!parent&&conversation?.chat?.id===turn.chat_id){
    const options=[{id:turn.id,kind:"turn",label:"최초 답변",text:turn.draft?.text||turn.template},...conversation.feedback.filter(f=>f.turn_id===turn.id).map(f=>({id:f.id,kind:"feedback",label:"담당자 수정 · "+f.created,text:f.edited})),...conversation.revisions.filter(r=>r.turn_id===turn.id&&r.draft).map(r=>({id:r.id,kind:"revision",label:"후속 답변 · "+r.created,text:r.draft.text}))];
    const [sw,si]=select("문서로 만들 원본",options.map(x=>[x.id,x.label]),turn.id);si.addEventListener("change",()=>{const source=options.find(x=>x.id===si.value);sourceKind=source.kind;sourceId=source.id;body.value=source.text;});editor.append(sw);
  }
  editor.append(factW,actionW,bodyW,requestW,el("p","근거와 원본 상태는 서버에서 붙입니다. 빈 사실·조치는 미확인으로 남으며 수행 완료로 바꾸지 않습니다."),handoffButton("문서 버전 저장",async()=>{
    const report=await api("/api/report/create",{mode:data.mode,turn_id:parent?.turn_id||turn.id,parent_id:parent?.id,source_kind:sourceKind,source_id:sourceId,title:title.value,content:body.value,facts:facts.value,actions:actions.value,request:request.value});handoffSelected=report.id;await renderHandoff();
  }));root.prepend(editor);editor.scrollIntoView({block:"start"});
}
$("#closeHandoff").addEventListener("click",()=>{handoffEpoch++;$("#handoffPanel").close();});
$(".sidebottom").prepend(handoffButton("문서 · 에이전트 전달함",()=>openHandoff()));
