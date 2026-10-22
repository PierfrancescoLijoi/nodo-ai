"""Scripted two-person meetings used to measure transcription, language identification and speaker
separation. Each turn is (speaker, text). Speaker B is the same native-language voice pitched down (ffmpeg rubberband), because the machine has
one voice per language and a foreign voice reading the text is unintelligible. So the
ground truth for "who spoke when" is known exactly."""

from __future__ import annotations

CONVERSATIONS: dict[str, dict] = {
    "en": {
        "locale": "en",
        "voices": {"A": "Microsoft Zira Desktop", "B": "Microsoft Zira Desktop"},
        "b_pitch": 0.78,
        "turns": [
            ("A", "Good morning everyone. Let's start with the ingestion pipeline, because the release is on the fourteenth of November."),
            ("B", "Thanks Sara. Last week we measured four minutes of latency when the recording is longer than one hour."),
            ("A", "That is too slow. What is the proposal?"),
            ("B", "We move the transcription to an asynchronous queue, so the app returns immediately and the worker processes the audio in the background."),
            ("A", "Agreed. We decided to use Azure Service Bus with a dead letter queue after five failed deliveries."),
            ("B", "I will write the migration for PostgreSQL and benchmark the graph queries with one million relations."),
            ("A", "Perfect. Marco, can you also check the cost of the speech service for ten hours of audio per user per month?"),
            ("B", "Yes, I will send the estimate by Friday. I expect about ten euros per user."),
            ("A", "Second topic. The Polish and Danish transcripts need a native speaker review before we show them to the pilot customers."),
            ("B", "I can ask Katarzyna for Polish, and Jakob for Danish. Both are available next week."),
            ("A", "Good. One more thing about privacy: every recording needs the attestation from the owner, and deletion must remove the graph too."),
            ("B", "That is already implemented. The erase job removes the audio, the transcript, the search documents and the relations."),
            ("A", "Great. Let's close here. Marco owns the benchmark, I own the pilot plan. Thank you all."),
            ("B", "Thank you, see you on Monday."),
        ],
    },
    "it": {
        "locale": "it",
        "voices": {"A": "Microsoft Elsa Desktop", "B": "Microsoft Elsa Desktop"},
        "b_pitch": 0.78,
        "turns": [
            ("A", "Buongiorno a tutti. Iniziamo dalla pipeline di ingestione, perché il rilascio è previsto per il quattordici novembre."),
            ("B", "Grazie Sara. La settimana scorsa abbiamo misurato quattro minuti di latenza quando la registrazione supera un'ora."),
            ("A", "Troppo lento. Qual è la proposta?"),
            ("B", "Spostiamo la trascrizione su una coda asincrona, così l'app risponde subito e il lavoratore elabora l'audio in background."),
            ("A", "D'accordo. Abbiamo deciso di usare Azure Service Bus con una coda di scarto dopo cinque consegne fallite."),
            ("B", "Scrivo io la migrazione per PostgreSQL e misuro le query del grafo con un milione di relazioni."),
            ("A", "Perfetto. Marco, puoi controllare anche il costo del servizio vocale per dieci ore di audio al mese per utente?"),
            ("B", "Sì, invio la stima entro venerdì. Mi aspetto circa dieci euro a utente."),
            ("A", "Secondo punto. Le trascrizioni in polacco e in danese hanno bisogno della revisione di un madrelingua prima di mostrarle ai clienti pilota."),
            ("B", "Posso chiedere a Katarzyna per il polacco e a Jakob per il danese. Entrambi sono disponibili la settimana prossima."),
            ("A", "Bene. Un'ultima cosa sulla privacy: ogni registrazione richiede l'attestazione del proprietario e la cancellazione deve rimuovere anche il grafo."),
            ("B", "È già implementato. Il processo di cancellazione elimina l'audio, la trascrizione, i documenti di ricerca e le relazioni."),
            ("A", "Ottimo. Chiudiamo qui. Marco è responsabile del benchmark, io del piano pilota. Grazie a tutti."),
            ("B", "Grazie, ci vediamo lunedì."),
        ],
    },
}
