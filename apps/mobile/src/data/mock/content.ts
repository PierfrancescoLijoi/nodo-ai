import type { Change, QuestionId, Segment } from '../types';
import type { Locale } from '@/i18n';

// Fictional sample content, authored per locale. Names and events are invented.
interface MeetingText {
  title: string;
  summary: string;
  decisions: string[];
  actionText: string;
  quote: string;
}

interface AnswerText {
  text: string;
  cites: [meetingId: string, segmentId: string][];
}

export interface LocaleContent {
  meetings: { m1: MeetingText; m2: MeetingText; m3: MeetingText };
  m1Quote2: string;
  changes: [string, string];
  answers: Record<QuestionId, AnswerText>;
  unanswerable: string;
  keywords: Record<QuestionId, string[]>;
  graph: Record<'ingestion' | 'queue' | 'syncFlow' | 'marco' | 'sara' | 'benchmark', { label: string; description: string }>;
}

export interface MeetingFacts {
  id: string;
  startedAt: string;
  durationMin: number;
  participants: string[];
  tags: string[];
  owner: string;
  dueDate: string;
  segment: Pick<Segment, 'id' | 'startMs' | 'speaker'>;
  actionId: string;
}

export const FACTS: MeetingFacts[] = [
  {
    id: 'm1', startedAt: '2026-10-21T10:30:00Z', durationMin: 42, participants: ['Marco', 'Sara'],
    tags: ['Architecture', 'Platform'], owner: 'Sara', dueDate: '2026-10-28',
    segment: { id: 'm1-s1', startMs: 762000, speaker: 'Marco' }, actionId: 'a1',
  },
  {
    id: 'm2', startedAt: '2026-10-14T14:00:00Z', durationMin: 35, participants: ['Sara', 'Marco'],
    tags: ['RAG', 'Evaluation'], owner: 'Marco', dueDate: '2026-10-21',
    segment: { id: 'm2-s1', startMs: 571000, speaker: 'Sara' }, actionId: 'a2',
  },
  {
    id: 'm3', startedAt: '2026-10-07T09:00:00Z', durationMin: 51, participants: ['Marco', 'Giulia'],
    tags: ['Ingestion', 'Architecture'], owner: 'Giulia', dueDate: '2026-10-14',
    segment: { id: 'm3-s1', startMs: 1083000, speaker: 'Giulia' }, actionId: 'a3',
  },
];

export const SECOND_SEGMENT = { id: 'm1-s2', startMs: 1695000, speaker: 'Sara' } as const;
export const CHANGE_FACTS: Pick<Change, 'id' | 'date' | 'meetingId'>[] = [
  { id: 'c1', date: '2026-10-21', meetingId: 'm1' },
  { id: 'c2', date: '2026-10-14', meetingId: 'm2' },
];

const en: LocaleContent = {
  meetings: {
    m1: {
      title: 'Platform architecture sync',
      summary: 'The team chose an asynchronous queue for the ingestion pipeline. The earlier synchronous proposal was set aside to absorb traffic spikes and allow retries.',
      decisions: ['Use an asynchronous queue for the ingestion pipeline.', 'Define idempotent retries and a dead-letter queue.'],
      actionText: 'Prepare the benchmark',
      quote: 'For ingestion we go with the async queue: it isolates the spikes and lets us replay jobs.',
    },
    m2: {
      title: 'RAG retrieval evaluation',
      summary: 'Discussion of hybrid queries and the criteria for judging answers.',
      decisions: ['Evaluate hybrid BM25 plus dense retrieval with reranking.'],
      actionText: 'Prepare a small golden set',
      quote: "Let's compare keyword and semantic retrieval and check how precise the citations are.",
    },
    m3: {
      title: 'Ingestion design review',
      summary: 'The first proposal was a synchronous flow for simplicity, with a risk under load.',
      decisions: ['Prototype the synchronous flow, then validate scalability.'],
      actionText: 'Collect throughput requirements',
      quote: "Let's start simple, but we check that synchronous processing holds up under peaks.",
    },
  },
  m1Quote2: 'I will prepare the benchmark by October 28, including retries and execution times.',
  changes: ['The asynchronous queue replaces the synchronous flow for ingestion.', 'Hybrid retrieval is the option to evaluate for RAG.'],
  answers: {
    ingestionChange: {
      text: 'On October 7 a synchronous flow was proposed for simplicity. On October 21 Marco chose an asynchronous queue to absorb spikes and retry jobs, so the queue is the current decision. Sara will prepare a benchmark by October 28.',
      cites: [['m3', 'm3-s1'], ['m1', 'm1-s1'], ['m1', 'm1-s2']],
    },
    saraTasks: {
      text: 'Sara owns the benchmark for the asynchronous ingestion queue, due October 28. It has to cover retries and execution times.',
      cites: [['m1', 'm1-s2']],
    },
    ragChoice: {
      text: 'On October 14 the team decided to evaluate hybrid retrieval, combining keyword and dense search with reranking. Marco is preparing a small golden set.',
      cites: [['m2', 'm2-s1']],
    },
  },
  unanswerable: "I cannot find enough evidence in your meetings to answer this reliably. Try the ingestion decision, RAG retrieval or Sara's deadline.",
  keywords: {
    ingestionChange: ['ingestion', 'queue', 'async', 'change', 'sync'],
    saraTasks: ['sara', 'deadline', 'benchmark', 'due'],
    ragChoice: ['rag', 'retrieval', 'golden', 'hybrid'],
  },
  graph: {
    ingestion: { label: 'Ingestion', description: 'Architecture topic discussed on October 7 and 21.' },
    queue: { label: 'Async queue', description: 'Chosen on October 21. Replaces the synchronous flow.' },
    syncFlow: { label: 'Sync flow', description: 'First proposal from October 7, now superseded.' },
    marco: { label: 'Marco', description: 'Proposed the asynchronous queue on October 21.' },
    sara: { label: 'Sara', description: 'Owns the agreed benchmark.' },
    benchmark: { label: 'Benchmark', description: 'Due October 28, covers retries and execution times.' },
  },
};

const pl: LocaleContent = {
  meetings: {
    m1: {
      title: 'Synchronizacja architektury platformy',
      summary: 'Zespół wybrał kolejkę asynchroniczną dla potoku ingestion. Wcześniejszą propozycję synchroniczną odłożono, aby absorbować szczyty ruchu i umożliwić ponowienia.',
      decisions: ['Użyć kolejki asynchronicznej dla potoku ingestion.', 'Zdefiniować idempotentne ponowienia i kolejkę martwych wiadomości.'],
      actionText: 'Przygotować benchmark',
      quote: 'W ingestion idziemy w kolejkę asynchroniczną: izoluje piki obciążenia i pozwala ponawiać zadania.',
    },
    m2: {
      title: 'Ocena wyszukiwania RAG',
      summary: 'Rozmowa o zapytaniach hybrydowych i kryteriach oceny odpowiedzi.',
      decisions: ['Ocenić wyszukiwanie hybrydowe BM25 z wektorowym i rerankingiem.'],
      actionText: 'Przygotować mały złoty zestaw testowy',
      quote: 'Porównajmy wyszukiwanie słów kluczowych i semantyczne oraz sprawdźmy precyzję cytowań.',
    },
    m3: {
      title: 'Przegląd projektu ingestion',
      summary: 'Pierwsza propozycja zakładała prosty przepływ synchroniczny, z ryzykiem przy dużym obciążeniu.',
      decisions: ['Zrobić prototyp przepływu synchronicznego, a potem zweryfikować skalowalność.'],
      actionText: 'Zebrać wymagania dotyczące przepustowości',
      quote: 'Zacznijmy prosto, ale sprawdźmy, czy przetwarzanie synchroniczne wytrzyma szczyty.',
    },
  },
  m1Quote2: 'Przygotuję benchmark do 28 października, z ponowieniami i czasami wykonania.',
  changes: ['Kolejka asynchroniczna zastępuje przepływ synchroniczny w ingestion.', 'Wyszukiwanie hybrydowe to opcja do oceny w RAG.'],
  answers: {
    ingestionChange: {
      text: '7 października zaproponowano przepływ synchroniczny dla prostoty. 21 października Marco wybrał kolejkę asynchroniczną, aby absorbować szczyty i ponawiać zadania, więc obowiązuje decyzja o kolejce. Sara przygotuje benchmark do 28 października.',
      cites: [['m3', 'm3-s1'], ['m1', 'm1-s1'], ['m1', 'm1-s2']],
    },
    saraTasks: {
      text: 'Sara odpowiada za benchmark kolejki asynchronicznej w ingestion, termin 28 października. Musi obejmować ponowienia i czasy wykonania.',
      cites: [['m1', 'm1-s2']],
    },
    ragChoice: {
      text: '14 października zespół postanowił ocenić wyszukiwanie hybrydowe, łączące wyszukiwanie słów kluczowych i wektorowe z rerankingiem. Marco przygotowuje mały złoty zestaw testowy.',
      cites: [['m2', 'm2-s1']],
    },
  },
  unanswerable: 'Nie znajduję w Twoich spotkaniach wystarczających dowodów, by odpowiedzieć rzetelnie. Spróbuj zapytać o decyzję w sprawie ingestion, wyszukiwanie RAG lub termin Sary.',
  keywords: {
    ingestionChange: ['ingestion', 'kolejk', 'asynchron', 'zmieni', 'decyzj'],
    saraTasks: ['sar', 'termin', 'benchmark', 'zadani'],
    ragChoice: ['rag', 'wyszukiw', 'retrieval'],
  },
  graph: {
    ingestion: { label: 'Ingestion', description: 'Temat architektoniczny omawiany 7 i 21 października.' },
    queue: { label: 'Kolejka async', description: 'Wybrana 21 października. Zastępuje przepływ synchroniczny.' },
    syncFlow: { label: 'Przepływ sync', description: 'Pierwsza propozycja z 7 października, obecnie zastąpiona.' },
    marco: { label: 'Marco', description: 'Zaproponował kolejkę asynchroniczną 21 października.' },
    sara: { label: 'Sara', description: 'Odpowiada za uzgodniony benchmark.' },
    benchmark: { label: 'Benchmark', description: 'Termin 28 października, obejmuje ponowienia i czasy wykonania.' },
  },
};

const it: LocaleContent = {
  meetings: {
    m1: {
      title: 'Sync architettura piattaforma',
      summary: 'Il team ha scelto una coda asincrona per la pipeline di ingestion. La proposta sincrona iniziale è stata messa da parte per assorbire i picchi e consentire i retry.',
      decisions: ['Usare una coda asincrona per la pipeline di ingestion.', 'Definire retry idempotenti e una dead-letter queue.'],
      actionText: 'Preparare il benchmark',
      quote: 'Per l’ingestion scegliamo la coda asincrona: isola i picchi e possiamo ripetere i job.',
    },
    m2: {
      title: 'Valutazione del retrieval RAG',
      summary: 'Discussione sulle query ibride e sui criteri di valutazione delle risposte.',
      decisions: ['Valutare il retrieval ibrido BM25 più denso con reranking.'],
      actionText: 'Preparare un piccolo golden set',
      quote: 'Confrontiamo keyword e semantic retrieval e verifichiamo la precisione delle citazioni.',
    },
    m3: {
      title: 'Design review dell’ingestion',
      summary: 'La prima proposta prevedeva un flusso sincrono per semplicità, con rischi sotto carico.',
      decisions: ['Prototipare il flusso sincrono, poi validare la scalabilità.'],
      actionText: 'Raccogliere i requisiti di throughput',
      quote: 'Partiamo in modo semplice, ma verifichiamo che l’elaborazione sincrona regga i picchi.',
    },
  },
  m1Quote2: 'Preparo il benchmark per il 28 ottobre, includendo retry e tempi di esecuzione.',
  changes: ['La coda asincrona sostituisce il flusso sincrono per l’ingestion.', 'Il retrieval ibrido è l’opzione da valutare per il RAG.'],
  answers: {
    ingestionChange: {
      text: 'Il 7 ottobre era stato proposto un flusso sincrono per semplicità. Il 21 ottobre Marco ha scelto una coda asincrona per assorbire i picchi e ripetere i job, quindi la decisione attuale è la coda. Sara preparerà un benchmark entro il 28 ottobre.',
      cites: [['m3', 'm3-s1'], ['m1', 'm1-s1'], ['m1', 'm1-s2']],
    },
    saraTasks: {
      text: 'Sara è responsabile del benchmark sulla coda asincrona di ingestion, con scadenza il 28 ottobre. Deve includere retry e tempi di esecuzione.',
      cites: [['m1', 'm1-s2']],
    },
    ragChoice: {
      text: 'Il 14 ottobre il team ha deciso di valutare il retrieval ibrido, che combina ricerca per parole chiave e densa con reranking. Marco sta preparando un piccolo golden set.',
      cites: [['m2', 'm2-s1']],
    },
  },
  unanswerable: 'Non trovo prove sufficienti nei tuoi meeting per rispondere in modo affidabile. Prova con la decisione sull’ingestion, il retrieval RAG o la scadenza di Sara.',
  keywords: {
    ingestionChange: ['ingestion', 'coda', 'asincron', 'cambi', 'decisione'],
    saraTasks: ['sara', 'scadenz', 'benchmark', 'responsab'],
    ragChoice: ['rag', 'retrieval', 'golden'],
  },
  graph: {
    ingestion: { label: 'Ingestion', description: 'Tema architetturale discusso il 7 e il 21 ottobre.' },
    queue: { label: 'Coda asincrona', description: 'Scelta il 21 ottobre. Sostituisce il flusso sincrono.' },
    syncFlow: { label: 'Flusso sincrono', description: 'Prima proposta del 7 ottobre, ora sostituita.' },
    marco: { label: 'Marco', description: 'Ha proposto la coda asincrona il 21 ottobre.' },
    sara: { label: 'Sara', description: 'Responsabile del benchmark concordato.' },
    benchmark: { label: 'Benchmark', description: 'Scadenza 28 ottobre, include retry e tempi di esecuzione.' },
  },
};

const da: LocaleContent = {
  meetings: {
    m1: {
      title: 'Arkitektursynk for platformen',
      summary: 'Teamet valgte en asynkron kø til ingestion-pipelinen. Det tidligere synkrone forslag blev lagt til side for at håndtere spidsbelastninger og muliggøre genforsøg.',
      decisions: ['Brug en asynkron kø til ingestion-pipelinen.', 'Definér idempotente genforsøg og en dead-letter-kø.'],
      actionText: 'Forbered benchmarket',
      quote: 'Til ingestion vælger vi den asynkrone kø: den isolerer spidsbelastninger, og vi kan afspille jobs igen.',
    },
    m2: {
      title: 'Evaluering af RAG-søgning',
      summary: 'Drøftelse af hybride forespørgsler og kriterierne for at vurdere svar.',
      decisions: ['Evaluér hybrid søgning med BM25 og tætte vektorer samt reranking.'],
      actionText: 'Forbered et lille golden set',
      quote: 'Lad os sammenligne nøgleords- og semantisk søgning og tjekke, hvor præcise citaterne er.',
    },
    m3: {
      title: 'Designgennemgang af ingestion',
      summary: 'Det første forslag var et synkront flow for enkelheds skyld, med risiko under belastning.',
      decisions: ['Lav en prototype af det synkrone flow, og valider derefter skalerbarheden.'],
      actionText: 'Indsaml krav til gennemløb',
      quote: 'Lad os starte enkelt, men vi tjekker, at synkron behandling kan klare spidsbelastninger.',
    },
  },
  m1Quote2: 'Jeg forbereder benchmarket til den 28. oktober, inklusive genforsøg og kørselstider.',
  changes: ['Den asynkrone kø erstatter det synkrone flow til ingestion.', 'Hybrid søgning er den mulighed, der skal evalueres til RAG.'],
  answers: {
    ingestionChange: {
      text: 'Den 7. oktober blev et synkront flow foreslået for enkelheds skyld. Den 21. oktober valgte Marco en asynkron kø for at håndtere spidsbelastninger og gentage jobs, så køen er den gældende beslutning. Sara forbereder et benchmark til den 28. oktober.',
      cites: [['m3', 'm3-s1'], ['m1', 'm1-s1'], ['m1', 'm1-s2']],
    },
    saraTasks: {
      text: 'Sara har ansvaret for benchmarket af den asynkrone ingestion-kø med frist den 28. oktober. Det skal dække genforsøg og kørselstider.',
      cites: [['m1', 'm1-s2']],
    },
    ragChoice: {
      text: 'Den 14. oktober besluttede teamet at evaluere hybrid søgning, der kombinerer nøgleords- og tæt søgning med reranking. Marco forbereder et lille golden set.',
      cites: [['m2', 'm2-s1']],
    },
  },
  unanswerable: 'Jeg kan ikke finde nok belæg i dine møder til at svare pålideligt. Prøv beslutningen om ingestion, RAG-søgning eller Saras frist.',
  keywords: {
    ingestionChange: ['ingestion', 'kø', 'asynkron', 'ændr', 'beslutning'],
    saraTasks: ['sara', 'frist', 'benchmark', 'opgave'],
    ragChoice: ['rag', 'søgning', 'retrieval'],
  },
  graph: {
    ingestion: { label: 'Ingestion', description: 'Arkitekturemne, der blev drøftet den 7. og 21. oktober.' },
    queue: { label: 'Asynkron kø', description: 'Valgt den 21. oktober. Erstatter det synkrone flow.' },
    syncFlow: { label: 'Synkront flow', description: 'Første forslag fra den 7. oktober, nu erstattet.' },
    marco: { label: 'Marco', description: 'Foreslog den asynkrone kø den 21. oktober.' },
    sara: { label: 'Sara', description: 'Ansvarlig for det aftalte benchmark.' },
    benchmark: { label: 'Benchmark', description: 'Frist den 28. oktober, dækker genforsøg og kørselstider.' },
  },
};

export const CONTENT: Record<Locale, LocaleContent> = { en, pl, it, da };
