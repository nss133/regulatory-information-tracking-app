import { ISSUE_CARDS, ISSUE_CARD_META } from "./issue-card-data.mjs";

const LAW_GROUPS = [
  ["금융소비자보호법", "금소법", "금융소비자 보호에 관한 법률"],
  ["자본시장과 금융투자업에 관한 법률", "자본시장법", "자통법"],
  ["금융회사의 지배구조에 관한 법률", "지배구조법", "금융사지배구조법"],
  ["신용정보의 이용 및 보호에 관한 법률", "신용정보법", "신정법"],
  ["개인정보 보호법", "개인정보보호법", "개보법"],
  ["특정 금융거래정보의 보고 및 이용 등에 관한 법률", "특정금융정보법", "특금법"],
  ["약관의 규제에 관한 법률", "약관규제법", "약관법"],
  ["독점규제 및 공정거래에 관한 법률", "공정거래법", "독점규제법"],
  ["금융실명거래 및 비밀보장에 관한 법률", "금융실명법"],
  ["전자금융거래법", "전금법"],
  ["전자상거래 등에서의 소비자보호에 관한 법률", "전자상거래법", "전상법"],
  ["채권의 공정한 추심에 관한 법률", "채권추심법", "채추법"],
  ["개인금융채권의 관리 및 개인금융채무자의 보호에 관한 법률", "개인채무자보호법"],
  ["표시·광고의 공정화에 관한 법률", "표시광고법"],
  ["하도급거래 공정화에 관한 법률", "하도급법"],
  ["근로자퇴직급여 보장법", "퇴직급여법", "근퇴법"],
  ["노동조합 및 노동관계조정법", "노조법", "노동조합법"],
  ["근로기준법", "근기법"],
  ["민사집행법"],
  ["채무자 회생 및 파산에 관한 법률", "채무자회생법"],
  ["보험업법"], ["상법"], ["민법"], ["신탁법"], ["형법"],
  ["보험업감독규정", "감독규정"],
  ["보험업감독업무시행세칙", "시행세칙"],
  ["전자금융감독규정"], ["금융투자업규정"],
];

// 「테스트셋 해시태그 (반출용).xlsx」에서 실제 사용된 태그를 우선 표기로 삼는다.
// 이 목록은 폐쇄형 허용 목록이 아니다. 기존 태그와 일치하거나 아래 별칭으로
// 정규화할 수 있을 때만 재사용하고, 그 밖의 법령·쟁점은 기존 규칙대로 생성한다.
const REFERENCE_TAG_GROUPS = [
  ["상속", "상속"], ["특별수익", "특별수익"], ["사망보험금", "사망보험금"],
  ["프로세스", "프로세스"], ["프로세스간소화", "프로세스 간소화", "절차 간소화", "업무 간소화"],
  ["미성년자", "미성년자"], ["법정대리권", "법정대리권", "법정 대리권"],
  ["보험금", "보험금"], ["면책", "면책", "면책사유", "보험금 면책사유"], ["무효", "무효"],
  ["보험료", "보험료"], ["납입최고", "납입최고", "납입 최고"],
  ["본인확인", "본인확인", "본인 확인"], ["모바일", "모바일"],
  ["계약내용의변경", "계약내용의 변경", "계약 내용의 변경", "계약내용 변경"], ["만기보험금", "만기보험금", "만기 보험금"],
  ["계약해지", "계약해지", "계약 해지"], ["통지", "통지"], ["수익증권", "수익증권"], ["질권", "질권"],
  ["압류", "압류"], ["체납처분", "체납처분", "체납 처분"],
  ["보험계약대출", "보험계약대출", "보험 계약 대출", "약관대출", "약관 대출"], ["상계", "상계"], ["공제", "공제"],
  ["고지의무위반", "고지의무 위반", "고지의무위반", "계약전 알릴 의무 위반", "계약전알릴의무위반"],
  ["사기취소", "사기취소", "사기 취소"], ["신용정보", "신용정보"], ["보유기간", "보유기간", "보유 기간"], ["파기", "파기"],
  ["수익자변경", "수익자변경", "수익자 변경"], ["유언", "유언"], ["설명의무", "설명의무", "설명 의무"],
  ["보험료할인", "보험료할인", "보험료 할인"], ["사업방법서", "사업방법서", "사업 방법서"],
  ["설명의무위반", "설명의무위반", "설명의무 위반"], ["손해배상", "손해배상", "손해 배상"], ["계약취소", "계약취소", "계약 취소"],
  ["명의정정", "명의정정", "명의 정정"], ["단체보험", "단체보험", "단체 보험"],
  ["해피콜", "해피콜"], ["자필서명", "자필서명", "자필 서명"], ["대출성상품", "대출성상품", "대출성 상품"],
  ["광고성정보전송", "광고성정보전송", "광고성 정보 전송"], ["마케팅수신동의", "마케팅수신동의", "마케팅 수신 동의"],
  ["약관교부", "약관교부", "약관 교부"], ["비대면", "비대면"], ["제지급", "제지급"], ["수령권자", "수령권자"],
  ["제3자", "제3자", "제 3자"], ["자금세탁", "자금세탁", "자금 세탁"], ["후견인", "후견인"], ["성년후견인", "성년후견인", "성년 후견인"],
  ["대리권", "대리권"], ["연금보험", "연금보험", "연금 보험"], ["일시금수령", "일시금수령", "일시금 수령"],
  ["상속재산분할심판", "상속재산분할심판", "상속재산 분할심판"], ["책임준비금", "책임준비금", "책임 준비금"], ["상속인", "상속인"],
  ["추심해지", "추심해지", "추심 해지"], ["보장성보험", "보장성보험", "보장성 보험"], ["압류금지", "압류금지", "압류 금지"],
  ["대리인본인확인", "대리인 본인확인", "대리인 본인 확인"], ["인수", "인수"], ["승낙전사고보험금", "승낙전 사고 보험금", "승낙 전 사고보험금"],
  ["타인의생명보험", "타인의 생명보험", "타인 생명보험"], ["피보험자서면동의철회", "피보험자 서면동의 철회", "피보험자 서면 동의 철회"],
  ["사정변경", "사정변경", "사정 변경"], ["부당권유행위", "부당권유행위", "부당권유 행위", "부당권유"], ["불완전판매", "불완전판매", "불완전 판매"],
  ["약관해석", "약관해석", "약관 해석"], ["제3자제공", "제3자 제공", "제3자제공", "개인정보 제3자 제공"],
  ["증서재발행", "증서재발행", "증서 재발행"], ["보험증권", "보험증권", "보험 증권"], ["법인계약", "법인계약", "법인 계약"],
  ["보험료반환", "보험료반환", "보험료 반환"], ["소멸시효", "소멸시효", "소멸 시효"], ["인지세", "인지세"],
  ["계약자변경", "계약자변경", "계약자 변경"], ["피보험자서면동의", "피보험자 서면동의", "피보험자 서면 동의"],
  ["감액", "감액"], ["실손전환", "실손전환", "실손 전환"], ["개인정보", "개인정보"], ["추심금", "추심금"],
  ["일시금지급", "일시금지급", "일시금 지급"], ["상속인동의", "상속인동의", "상속인 동의"], ["교부", "교부"],
  ["계약서류제공", "계약서류 제공", "계약 서류 제공"], ["AI음성봇", "AI 음성봇", "AI음성봇"],
  ["인공지능기본법", "인공지능기본법", "인공지능 기본법"], ["인수심사", "인수심사", "인수 심사"],
  ["종피보험자", "종피보험자"], ["처분금지효", "처분금지효", "처분 금지효"], ["사망시수익자", "사망시 수익자", "사망 시 수익자"],
  ["현금", "현금"], ["보험료수납", "보험료수납", "보험료 수납"], ["보험료납입", "보험료납입", "보험료 납입"],
  ["청약거절", "청약 거절", "청약거절", "청약을 거절", "인수 거절", "인수거절"],
  ["승낙전사고보험금", "승낙 전 보험사고", "승낙전 보험사고", "승낙 전 사고", "승낙전사고"],
  ["재해", "재해", "재해사고", "재해 사고"],
  ["자유로운의사결정", "자유로운 의사결정", "자유로운 의사 결정", "자유 의사결정", "자유로운 의사"],
].map(([canonical, ...terms]) => ({ canonical, terms }));

const REFERENCE_PROVISION_TAGS = [
  ["상법제638조", "상법", "638", ""], ["상법제638조의2", "상법", "638", "2"], ["상법제638조의3", "상법", "638", "3"],
  ["상법제644조", "상법", "644", ""], ["상법제649조", "상법", "649", ""], ["상법제731조", "상법", "731", ""],
  ["상법제733조", "상법", "733", ""], ["상법제733조제3항", "상법", "733", "", "3"],
  ["보험업법제95조의2", "보험업법", "95", "2"], ["금소법제19조", "금융소비자보호법", "19", ""],
  ["금소법제21조", "금융소비자보호법", "21", ""], ["금소법제23조", "금융소비자보호법", "23", ""],
  ["전자금융감독규정제15조", "전자금융감독규정", "15", ""],
];

const CONCEPT_GROUPS = [
  ["legal_relation", "보험계약", "보험계약"],
  ["legal_relation", "위탁", "업무위탁", "위탁"],
  ["legal_relation", "재위탁", "재위탁"],
  ["legal_relation", "개인정보 제3자 제공", "개인정보 제3자 제공", "제3자 제공"],
  ["legal_relation", "근로계약", "근로계약"],
  ["legal_relation", "손해배상", "손해배상"],
  ["legal_relation", "채무불이행", "채무불이행"],
  ["legal_relation", "불법행위", "불법행위"],
  ["legal_relation", "부당이득", "부당이득"],
  ["legal_relation", "상속", "상속"],
  ["legal_relation", "신탁", "신탁"],
  ["issue", "설명의무", "설명의무", "설명 의무"],
  ["issue", "고지의무", "고지의무", "고지 의무"],
  ["issue", "통지의무", "통지의무", "통지 의무"],
  ["issue", "적합성원칙", "적합성원칙", "적합성 원칙", "적합성"],
  ["issue", "적정성원칙", "적정성원칙", "적정성 원칙", "적정성"],
  ["issue", "부당권유", "부당권유"], ["issue", "특별이익", "특별이익"],
  ["issue", "청약철회", "청약철회", "청약 철회"],
  ["issue", "위법계약해지권", "위법계약해지권", "위법계약 해지"],
  ["issue", "이해상충", "이해상충", "이해 상충"],
  ["issue", "내부통제", "내부통제", "내부 통제"],
  ["issue", "준법감시", "준법감시", "준법 감시"],
  ["issue", "개인정보", "개인정보"], ["issue", "신용정보", "개인신용정보", "신용정보"],
  ["issue", "동의", "사전 동의", "동의"], ["issue", "국외이전", "국외이전", "국외 이전"],
  ["issue", "소멸시효", "소멸시효", "소멸 시효"], ["issue", "제척기간", "제척기간", "제척 기간"],
  ["issue", "면책사유", "면책사유", "면책 사유"], ["issue", "인과관계", "인과관계", "인과 관계"],
  ["issue", "과실상계", "과실상계", "과실 상계"], ["issue", "이사회 승인", "이사회 승인", "이사회 의결"],
  ["issue", "대규모내부거래", "대규모내부거래", "대규모 내부거래"], ["issue", "공시의무", "공시의무", "공시 의무"],
  ["action", "보험모집", "보험모집", "보험 모집"], ["action", "광고", "공동광고", "금융상품 광고", "광고"],
  ["action", "정보제공", "정보 제공", "정보제공"],
  ["subject", "금융소비자", "금융소비자", "금융 소비자"], ["subject", "보험계약자", "보험계약자", "보험 계약자"],
  ["subject", "피보험자", "피보험자"], ["subject", "보험수익자", "보험수익자", "보험 수익자"],
  ["subject", "위탁자", "위탁자"], ["subject", "수탁자", "수탁자"], ["subject", "임원", "임원"], ["subject", "이사회", "이사회"],
  ["risk", "과태료", "과태료"], ["risk", "과징금", "과징금"], ["risk", "형사처벌", "형사처벌", "형사 처벌"],
  ["risk", "행정제재", "행정제재", "행정 제재", "제재 위험"], ["risk", "손해배상책임", "손해배상책임", "손해배상 책임"],
  ["risk", "계약무효", "계약 무효", "무효 위험"], ["risk", "시정명령", "시정명령", "시정 명령"],
].map(([type, canonical, ...terms]) => ({ type, canonical, terms }));

const CONCLUSION_RULES = [
  ["사전승인필요", /사전\s*승인(?:이|을)?\s*필요/g, /승인을\s*(?:받아야|요함)/g],
  ["이사회승인필요", /이사회\s*(?:승인|의결)(?:이|을)?\s*필요/g, /이사회에서\s*의결/g],
  ["신고필요", /신고(?:가|를)?\s*필요/g, /신고하여야/g],
  ["동의필요", /동의(?:가|를)?\s*필요/g, /동의를\s*(?:받아야|얻어야)/g],
  ["규정개정필요", /(?:사규|규정|내규)(?:의|를)?\s*(?:개정|정비)(?:할|가)?\s*필요/g],
  ["추가검토필요", /추가\s*검토(?:가|를)?\s*필요/g, /별도\s*확인(?:이|을)?\s*필요/g],
  ["금지", /(?:허용되지\s*않|금지되|하여서는\s*아니)/g],
  ["허용가능", /(?:허용\s*가능|가능할\s*것으로\s*판단|가능한\s*것으로\s*판단)/g],
  ["위반소지", /(?:위반\s*소지|위반될\s*가능성|위반에\s*해당)/g],
  ["비해당", /(?:해당하지\s*않|적용되지\s*않)/g],
].map(([canonical, ...patterns]) => ({ canonical, patterns }));

const TYPE_LABELS = { law: "법령·규정", provision: "법령·조문", legal_relation: "법률관계", issue: "핵심쟁점", conclusion: "결론·효과", action: "행위", subject: "주체·대상", risk: "리스크" };
const PREFERRED_REFERENCE_LABELS = new Map([
  ["개인정보 제3자 제공", "제3자제공"],
  ["부당권유", "부당권유행위"],
]);
const FORMAL_REFERENCE_TAGS = new Set(["프로세스", "프로세스간소화", "본인확인", "모바일", "교부"]);
const CONCRETE_REFERENCE_TAGS = new Set(["청약거절", "승낙전사고보험금", "재해", "사망보험금", "자유로운의사결정", "고지의무위반", "설명의무위반", "불완전판매"]);
const REQUEST_HEADINGS = /^(?:\d+[.)]\s*)?(?:신청\s*내용|질의\s*내용|검토\s*요청|질의\s*사항|요청\s*사항)\s*[:：]?\s*$/i;
const RESULT_HEADINGS = /^(?:\d+[.)]\s*)?(?:검토\s*결과|검토\s*의견|검토결과\s*및\s*의견|결론|소결|종합\s*의견)\s*[:：]?\s*$/i;
const CARD_TOKEN_STOPWORDS = new Set(["관련", "경우", "대한", "위한", "따른", "규제", "기준", "구분", "적용", "관리", "의무", "보호", "사항", "범위", "원칙", "절차", "제한", "요건", "방식"]);

function compact(value) {
  return String(value).toLocaleLowerCase("ko-KR").replace(/[^0-9a-z가-힣]/g, "");
}

function sourceWeight(source, request = 2, result = 6, title = 10) {
  if (source === "제목") return title;
  if (source === "결과") return result;
  if (source === "신청부서의견") return request - 6;
  return request;
}

function cardArticle(card) {
  return card.anchor.split("|").slice(1).join("|").match(/제\s*(\d+)\s*조(?:\s*의\s*(\d+))?/)?.slice(1, 3) ?? null;
}

function lawGroupForCard(card) {
  const anchorLaw = card.anchor.split("|")[0];
  return LAW_GROUPS.find((group) => group.some((alias) => compact(alias) === compact(card.law) || compact(alias) === compact(anchorLaw))) ?? [anchorLaw, card.law];
}

function cardPreferredReference(card) {
  const titleText = compact(card.title);
  const gistText = compact(card.gist);
  let best = null;
  for (const group of REFERENCE_TAG_GROUPS) {
    for (const term of [group.canonical, ...group.terms]) {
      const normalized = compact(term);
      const titleMatch = normalized.length >= 3 && titleText.includes(normalized);
      const concreteGistMatch = normalized.length >= 5 && CONCRETE_REFERENCE_TAGS.has(group.canonical) && gistText.includes(normalized);
      if ((titleMatch || concreteGistMatch) && (!best || Number(titleMatch) > Number(best.titleMatch) || (titleMatch === best.titleMatch && normalized.length > best.length))) {
        best = { canonical: group.canonical, terms: [group.canonical, ...group.terms], titleMatch, length: normalized.length };
      }
    }
  }
  return best;
}

let issueCardProfiles;

function getIssueCardProfiles() {
  if (issueCardProfiles) return issueCardProfiles;
  issueCardProfiles = ISSUE_CARDS.map((card) => {
    const preferred = cardPreferredReference(card);
    const referenceTerms = preferred ? Array.from(new Set([preferred.canonical, ...preferred.terms])).map((term) => ({ term, compact: compact(term) })).filter((item) => item.compact).sort((a, b) => b.compact.length - a.compact.length) : [];
    const titleTokens = Array.from(new Set(card.title.match(/[0-9A-Za-z가-힣]+/g)?.filter((token) => token.length >= 2 && !CARD_TOKEN_STOPWORDS.has(token)).map(compact) ?? []));
    return {
      card,
      article: cardArticle(card),
      aliases: lawGroupForCard(card),
      preferred,
      referenceTerms,
      compactTitle: compact(card.title),
      titleTokens,
      fallbackLabel: fallbackCardLabel(card),
    };
  });
  return issueCardProfiles;
}

function profileReferenceMatch(profile, compactText) {
  if (!profile.preferred) return null;
  const matched = profile.referenceTerms.find((item) => compactText.includes(item.compact));
  return matched ? { ...profile.preferred, alias: matched.term } : null;
}

function profileTitleRelevance(profile, compactText) {
  const hits = profile.titleTokens.filter((token) => compactText.includes(token));
  return { hits: hits.length, coverage: profile.titleTokens.length ? hits.length / profile.titleTokens.length : 0, exact: profile.compactTitle.length >= 5 && compactText.includes(profile.compactTitle) };
}

function fallbackCardLabel(card) {
  return card.title.replace(/[—–].*$/, "").replace(/\([^)]*\)\s*$/, "").replace(/\s+/g, " ").trim();
}

function cardMapping(card, relation) {
  return { id: card.id, law: card.law, anchor: card.anchor, title: card.title, opSupport: card.opSupport, relation, sourceDate: ISSUE_CARD_META.sourceDate };
}

function findAll(text, term, preparedLowerText) {
  const out = [], haystack = preparedLowerText ?? text.toLocaleLowerCase("ko-KR"), needle = term.toLocaleLowerCase("ko-KR");
  let from = 0;
  while (needle && from < haystack.length) {
    const at = haystack.indexOf(needle, from);
    if (at < 0) break;
    out.push(at); from = at + Math.max(needle.length, 1);
  }
  return out;
}

function excerpt(text, at, length) {
  return text.slice(Math.max(0, at - 42), Math.min(text.length, at + length + 58)).replace(/\s+/g, " ").trim();
}

function sectionBonus(text, at) {
  const before = text.slice(0, at);
  const lines = before.split(/\r?\n/);
  const nearestHeading = [...lines].reverse().find((line) => line.trim() && (/^(?:\d+[.)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]|[가-힣][.)])\s*/.test(line.trim()) || line.trim().length <= 36));
  if (nearestHeading && RESULT_HEADINGS.test(nearestHeading.trim())) return 15;
  if (nearestHeading && /(결론|소결|종합\s*의견|검토\s*결과)/.test(nearestHeading)) return 15;
  if (nearestHeading && /(쟁점|질의|검토\s*사항|검토\s*의견)/.test(nearestHeading)) return 10;
  return 0;
}

function titleBonus(text, at) {
  const lineStart = text.lastIndexOf("\n", Math.max(0, at - 1)) + 1;
  const lineEndAt = text.indexOf("\n", at);
  const line = text.slice(lineStart, lineEndAt < 0 ? text.length : lineEndAt).trim();
  const headingShape = /^(?:#{1,6}\s+|\d+[.)]\s*|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]\s*|[가-힣][.)]\s*)/.test(line);
  return line.length <= 70 && (headingShape || /(?:쟁점|질의|결론|검토\s*결과|검토\s*의견)\s*[:：]?$/.test(line)) ? 12 : 0;
}

export function splitLegalDocument(fullText = "") {
  const text = String(fullText).replace(/\r\n?/g, "\n").trim();
  if (!text) return { fullText: "", requestText: "", resultText: "", method: "empty" };
  const lines = text.split("\n");
  let requestHeading = -1;
  let resultHeading = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (requestHeading < 0 && REQUEST_HEADINGS.test(line)) requestHeading = index;
    if (RESULT_HEADINGS.test(line)) { resultHeading = index; break; }
  }
  if (resultHeading >= 0) {
    const requestStart = requestHeading >= 0 && requestHeading < resultHeading ? requestHeading + 1 : 0;
    return {
      fullText: text,
      requestText: lines.slice(requestStart, resultHeading).join("\n").trim(),
      resultText: lines.slice(resultHeading + 1).join("\n").trim(),
      method: "heading",
    };
  }
  const resultSignal = /(?:^|\n)\s*(?:검토\s*결과|결론|따라서|종합하면)\s*[,：:]?/m.exec(text);
  if (resultSignal && resultSignal.index > 0) {
    return { fullText: text, requestText: text.slice(0, resultSignal.index).trim(), resultText: text.slice(resultSignal.index).trim(), method: "signal" };
  }
  return { fullText: text, requestText: text, resultText: "", method: "single" };
}

function toHashtag(value) {
  return "#" + value.replace(/[§]/g, "제").replace(/\s+/g, "_").replace(/[()[\]{}.,:;·ㆍ/\\]/g, "").replace(/_+/g, "_").replace(/^_|_$/g, "");
}

function addCandidate(store, candidate) {
  candidate = { ...candidate, canonical: PREFERRED_REFERENCE_LABELS.get(candidate.canonical) ?? candidate.canonical };
  const key = candidate.type + ":" + candidate.canonical, current = store.get(key);
  if (!current) {
    store.set(key, { ...candidate, rawScore: candidate.score, sources: new Set([candidate.source]), evidence: [candidate.evidence], aliases: new Set(candidate.alias ? [candidate.alias] : []), occurrences: 1, cardMappings: candidate.cardMapping ? [candidate.cardMapping] : [] });
    return;
  }
  current.rawScore = candidate.cardMapping ? Math.max(current.rawScore, candidate.score) : Math.max(current.rawScore, candidate.score) + Math.min(3.5, candidate.score * 0.025);
  current.sources.add(candidate.source);
  if (!candidate.cardMapping) current.occurrences += 1;
  if (candidate.alias) current.aliases.add(candidate.alias);
  if (!current.evidence.some((item) => item.source === candidate.evidence.source && item.text === candidate.evidence.text)) current.evidence.push(candidate.evidence);
  if (candidate.status === "직접 후보") current.status = "직접 후보";
  if (candidate.cardMapping && !current.cardMappings.some((mapping) => mapping.id === candidate.cardMapping.id)) current.cardMappings.push(candidate.cardMapping);
}

function scanLawGroup(store, text, source, group, lowerText) {
  const canonical = group[0];
  for (const alias of group) for (const at of findAll(text, alias, lowerText)) {
    const boost = sourceWeight(source, 1, 5, 9) + sectionBonus(text, at) + titleBonus(text, at);
    addCandidate(store, { canonical, type: "law", score: 82 + boost, source, alias, evidence: { source, text: excerpt(text, at, alias.length), at } });
    const after = text.slice(at + alias.length, at + alias.length + 90);
    const pattern = /(?:법|규정|규칙|세칙)?\s*(?:제|§)?\s*(\d+)\s*조(?:의\s*(\d+))?(?:\s*(?:제)?\s*(\d+)\s*항)?/g;
    let match;
    while ((match = pattern.exec(after))) {
      const article = "제" + match[1] + "조" + (match[2] ? "의" + match[2] : "") + (match[3] ? " 제" + match[3] + "항" : "");
      addCandidate(store, { canonical: canonical + " " + article, type: "provision", score: 94 + boost, source, alias: alias + " " + article, evidence: { source, text: excerpt(text, at, alias.length + match.index + match[0].length), at } });
    }
  }
}

function scanConcept(store, text, source, group, lowerText) {
  const bases = { legal_relation: 74, issue: 76, action: 65, subject: 63, risk: 73 };
  for (const term of group.terms) for (const at of findAll(text, term, lowerText)) {
    addCandidate(store, { canonical: group.canonical, type: group.type, score: bases[group.type] + sourceWeight(source) + sectionBonus(text, at) + titleBonus(text, at) + Math.min(5, term.length / 3), source, alias: term, evidence: { source, text: excerpt(text, at, term.length), at } });
  }
}

function scanReferenceTags(store, text, source, lowerText) {
  for (const group of REFERENCE_TAG_GROUPS) for (const term of group.terms) for (const at of findAll(text, term, lowerText)) {
    const specificityBase = CONCRETE_REFERENCE_TAGS.has(group.canonical) ? 82 : FORMAL_REFERENCE_TAGS.has(group.canonical) ? 56 : 70;
    addCandidate(store, { canonical: group.canonical, type: "issue", score: specificityBase + sourceWeight(source) + sectionBonus(text, at) + titleBonus(text, at) + Math.min(6, term.length / 3), source, alias: term, evidence: { source, text: excerpt(text, at, term.length), at } });
  }
  for (const [canonical, law, article, subArticle, paragraph] of REFERENCE_PROVISION_TAGS) {
    const articleText = `제${article}조${subArticle ? `의${subArticle}` : ""}${paragraph ? ` 제${paragraph}항` : ""}`;
    const aliases = [canonical, `${law}${articleText}`, `${law} ${articleText}`];
    if (law === "금융소비자보호법") aliases.push(`금소법${articleText}`, `금소법 ${articleText}`);
    for (const alias of aliases) for (const at of findAll(text, alias, lowerText)) {
      const tail = text.slice(at + alias.length, at + alias.length + 18);
      if (!subArticle && /^\s*의\s*\d/.test(tail)) continue;
      if (!paragraph && /^\s*(?:제\s*)?\d+\s*항/.test(tail)) continue;
      addCandidate(store, { canonical, type: "provision", score: 98 + sourceWeight(source, 1, 5, 9) + sectionBonus(text, at) + titleBonus(text, at), source, alias, evidence: { source, text: excerpt(text, at, alias.length), at } });
    }
  }
}

function scanConclusions(store, text, source) {
  for (const rule of CONCLUSION_RULES) for (const pattern of rule.patterns) {
    pattern.lastIndex = 0; let match;
    while ((match = pattern.exec(text))) {
      addCandidate(store, { canonical: rule.canonical, type: "conclusion", score: 82 + sourceWeight(source, 1, 8, 10) + sectionBonus(text, match.index) + titleBonus(text, match.index), source, alias: match[0], evidence: { source, text: excerpt(text, match.index, match[0].length), at: match.index } });
      if (!match[0].length) pattern.lastIndex += 1;
    }
  }
}

function scanIssueCards(store, text, source, lowerText, compactText) {
  const explicitGroups = new Map();
  for (const profile of getIssueCardProfiles()) {
    const { card, article, aliases } = profile;
    if (!article) continue;
    const [number, subNumber] = article;
    const articlePattern = new RegExp(`제\\s*${number}\\s*조${subNumber ? `(?:\\s*의\\s*${subNumber})` : ""}`, "g");
    const articleMatches = Array.from(text.matchAll(articlePattern));
    if (!articleMatches.length) continue;
    const lawPositions = aliases.flatMap((alias) => findAll(text, alias, lowerText).map((at) => ({ at, alias })));
    const nearest = lawPositions.flatMap((law) => articleMatches.map((match) => ({ law, articleAt: match.index ?? 0, distance: Math.abs(law.at - (match.index ?? 0)) }))).sort((a, b) => a.distance - b.distance)[0];
    if (!nearest || nearest.distance > 160) continue;
    const key = compact(aliases[0]) + `:${number}:${subNumber || ""}:${source}:${nearest.law.at}:${nearest.articleAt}`;
    if (!explicitGroups.has(key)) explicitGroups.set(key, []);
    explicitGroups.get(key).push({ card, profile, at: Math.min(nearest.law.at, nearest.articleAt), alias: `${nearest.law.alias} 제${number}조${subNumber ? `의${subNumber}` : ""}` });
  }

  for (const matches of explicitGroups.values()) {
    const ranked = matches.map((match) => ({ ...match, reference: match.profile.preferred, relevance: profileTitleRelevance(match.profile, compactText) }))
      .sort((a, b) => Number(b.reference != null) - Number(a.reference != null) || Number(b.relevance.exact) - Number(a.relevance.exact) || b.relevance.hits - a.relevance.hits || b.card.opSupport - a.card.opSupport)
      .slice(0, 2);
    for (const match of ranked) {
      const label = match.reference?.canonical || fallbackCardLabel(match.card);
      const supportBonus = Math.min(7, Math.log2(match.card.opSupport + 1) * 2);
      const relevanceBonus = (match.reference ? 6 : 0) + (match.relevance.exact ? 5 : Math.min(4, match.relevance.hits * 2));
      addCandidate(store, {
        canonical: label,
        type: "issue",
        score: 76 + supportBonus + relevanceBonus + sourceWeight(source) + sectionBonus(text, match.at) + titleBonus(text, match.at),
        source,
        alias: match.reference?.canonical || match.alias,
        evidence: { source, text: excerpt(text, match.at, match.alias.length), at: match.at },
        status: "연관 후보",
        cardMapping: cardMapping(match.card, "법령·조문 연결"),
      });
    }
  }

  for (const profile of getIssueCardProfiles()) {
    const { card } = profile;
    if (card.anchor.includes("핵심쟁점 지도")) continue;
    const reference = profileReferenceMatch(profile, compactText);
    const relevance = profileTitleRelevance(profile, compactText);
    if (!reference && !relevance.exact && !(relevance.hits >= 2 && relevance.coverage >= 0.6)) continue;
    const label = reference?.canonical || profile.fallbackLabel;
    const alias = reference?.alias || card.title;
    const at = Math.max(0, lowerText.indexOf(alias.toLocaleLowerCase("ko-KR")));
    addCandidate(store, {
      canonical: label,
      type: "issue",
      score: 73 + (reference ? 7 : 0) + (relevance.exact ? 7 : Math.min(6, relevance.hits * 2)) + Math.min(5, Math.log2(card.opSupport + 1) * 1.5) + sourceWeight(source) + sectionBonus(text, at) + titleBonus(text, at),
      source,
      alias,
      evidence: { source, text: excerpt(text, at, alias.length), at },
      status: "직접 후보",
      cardMapping: cardMapping(card, reference ? "쟁점 용어 일치" : "카드 제목 일치"),
    });
  }
}

export function generateTagAnalysis(titleText = "", requestText = "", resultText = "", departmentOpinionText = "") {
  const documents = [{ source: "제목", text: String(titleText).trim() }, { source: "신청", text: String(requestText).trim() }, { source: "결과", text: String(resultText).trim() }, { source: "신청부서의견", text: String(departmentOpinionText).trim() }].filter((item) => item.text);
  const store = new Map();
  for (const document of documents) {
    const lowerText = document.text.toLocaleLowerCase("ko-KR"), compactText = compact(document.text);
    scanReferenceTags(store, document.text, document.source, lowerText);
    for (const group of LAW_GROUPS) scanLawGroup(store, document.text, document.source, group, lowerText);
    for (const group of CONCEPT_GROUPS) scanConcept(store, document.text, document.source, group, lowerText);
    scanConclusions(store, document.text, document.source);
    scanIssueCards(store, document.text, document.source, lowerText, compactText);
  }
  const rawCandidates = Array.from(store.values()).map((item) => ({
    canonical: item.canonical,
    hashtag: toHashtag(item.canonical),
    type: item.type,
    typeLabel: TYPE_LABELS[item.type],
    rawScore: Math.round(item.rawScore),
    sources: Array.from(item.sources),
    aliases: Array.from(item.aliases),
    evidence: item.evidence.slice(0, 8),
    occurrences: item.occurrences,
    status: item.status || "직접 후보",
    cardMappings: item.cardMappings.slice(0, 5),
  })).sort((a, b) => b.rawScore - a.rawScore || a.hashtag.localeCompare(b.hashtag, "ko"));
  const ranked = Array.from(store.values()).map((item) => {
    const frequencyBonus = Math.min(15, Math.max(0, item.occurrences - 1) * 2.2);
    const score = Math.min(100, Math.round(item.rawScore + (item.sources.size > 1 ? 8 : 0) + frequencyBonus));
    return { id: item.type + "-" + item.canonical, label: item.canonical, hashtag: toHashtag(item.canonical), type: item.type, typeLabel: TYPE_LABELS[item.type], score, confidence: score >= 88 ? "높음" : score >= 72 ? "보통" : "검토", sources: Array.from(item.sources), aliases: Array.from(item.aliases), evidence: item.evidence.slice(0, 5), occurrences: item.occurrences, status: item.status || "직접 후보", cardMappings: item.cardMappings.slice(0, 5) };
  }).filter((item) => item.score >= 58).sort((a, b) => b.score - a.score || Number(b.type === "provision") - Number(a.type === "provision") || a.type.localeCompare(b.type, "ko") || a.label.localeCompare(b.label, "ko"));
  const seen = new Set();
  const normalizedTags = ranked.filter((item) => {
    const identity = item.type === "provision" ? `provision:${compact(item.label)}` : item.hashtag;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
  return {
    rawCandidates,
    normalizedTags,
    relations: buildTagRelations(normalizedTags),
    strategies: {
      strict: normalizedTags.filter((tag) => tag.score >= 88 && tag.status !== "연관 후보"),
      wide: rawCandidates,
      layered: normalizedTags,
    },
  };
}

export function generateTags(requestText = "", resultText = "", titleText = "", departmentOpinionText = "") {
  return generateTagAnalysis(titleText, requestText, resultText, departmentOpinionText).normalizedTags;
}

export function buildTagConnections(tags = []) {
  const edges = [];
  for (let leftIndex = 0; leftIndex < tags.length; leftIndex += 1) for (let rightIndex = leftIndex + 1; rightIndex < tags.length; rightIndex += 1) {
    const left = tags[leftIndex], right = tags[rightIndex];
    const sharedSources = left.sources.filter((source) => right.sources.includes(source));
    const leftMappings = left.cardMappings || [], rightMappings = right.cardMappings || [];
    const cardLinks = new Set(leftMappings.filter((mapping) => rightMappings.some((other) => other.id === mapping.id)).map((mapping) => mapping.anchor));
    const provisionConnects = (tag, mapping) => {
      if (tag.type !== "provision") return false;
      const article = mapping.anchor.match(/제\s*\d+\s*조(?:\s*의\s*\d+)?/)?.[0];
      if (!article || !compact(tag.label).includes(compact(article))) return false;
      return lawGroupForCard({ law: mapping.law, anchor: mapping.anchor }).some((alias) => compact(tag.label).includes(compact(alias)));
    };
    for (const mapping of leftMappings) if (provisionConnects(right, mapping)) cardLinks.add(mapping.anchor);
    for (const mapping of rightMappings) if (provisionConnects(left, mapping)) cardLinks.add(mapping.anchor);
    if (!sharedSources.length && !cardLinks.size) continue;
    let minDistance = Infinity;
    for (const leftEvidence of left.evidence) for (const rightEvidence of right.evidence) {
      if (leftEvidence.source === rightEvidence.source) minDistance = Math.min(minDistance, Math.abs(leftEvidence.at - rightEvidence.at));
    }
    const sourceScore = 35 * (sharedSources.length / new Set([...left.sources, ...right.sources]).size);
    const proximityScore = Number.isFinite(minDistance) ? 40 * Math.max(0, 1 - minDistance / 900) : 0;
    const typeScore = left.type !== right.type ? 10 : 4;
    const confidenceScore = (left.score + right.score) / 20;
    const cardScore = cardLinks.size ? 22 : 0;
    const score = Math.min(100, Math.round(sourceScore + proximityScore + typeScore + confidenceScore + cardScore));
    if (score >= 35) edges.push({ sourceId: left.id, sourceTag: left.hashtag, targetId: right.id, targetTag: right.hashtag, score, sharedSources, proximity: Number.isFinite(minDistance) ? minDistance : null, cardLinks: Array.from(cardLinks) });
  }
  return edges.sort((a, b) => b.score - a.score || a.sourceTag.localeCompare(b.sourceTag, "ko"));
}

export function buildTagRelations(tags = []) {
  const edges = buildTagConnections(tags);
  const relations = [];
  for (const edge of edges) {
    for (const anchor of edge.cardLinks || []) relations.push({ sourceTag: edge.sourceTag, targetTag: edge.targetTag, relation: "핵심쟁점카드", score: edge.score, evidence: anchor });
  }
  for (const conclusion of tags.filter((tag) => tag.type === "conclusion")) {
    const linked = edges.filter((edge) => edge.sourceId === conclusion.id || edge.targetId === conclusion.id)
      .map((edge) => ({ edge, other: tags.find((tag) => tag.id === (edge.sourceId === conclusion.id ? edge.targetId : edge.sourceId)) }))
      .filter((item) => item.other && ["issue", "legal_relation", "provision", "action"].includes(item.other.type))
      .sort((a, b) => b.edge.score - a.edge.score)
      .slice(0, 3);
    for (const { edge, other } of linked) relations.push({ sourceTag: other.hashtag, targetTag: conclusion.hashtag, relation: "결론", score: edge.score, evidence: conclusion.evidence[0]?.text || "" });
  }
  const seen = new Set();
  return relations.filter((relation) => {
    const key = `${relation.sourceTag}|${relation.relation}|${relation.targetTag}|${relation.evidence}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function summarizeTags(tags) {
  return { total: tags.length, high: tags.filter((tag) => tag.confidence === "높음").length, laws: tags.filter((tag) => tag.type === "law" || tag.type === "provision").length, issues: tags.filter((tag) => tag.type === "issue" || tag.type === "legal_relation").length };
}

export const TAG_TYPE_LABELS = TYPE_LABELS;
