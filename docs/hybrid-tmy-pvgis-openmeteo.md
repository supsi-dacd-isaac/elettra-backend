# TMY ibrido PVGIS–Open‑Meteo

## Scopo

Elettra usa una serie meteorologica oraria annuale per il clustering delle temperature e per costruire gli scenari delle analisi annuali. Non serve un file EPW completo: serve soprattutto una serie `temp_air` rappresentativa del luogo in cui opera l'agenzia.

Il metodo ibrido mantiene la capacità di PVGIS di scegliere dodici mesi meteorologicamente tipici, ma sostituisce la temperatura PVGIS con `temperature_2m` di Open‑Meteo Archive. L'obiettivo è ridurre gli errori locali dovuti alla griglia relativamente ampia di PVGIS, particolarmente importanti in Svizzera quando montagne vicine alterano l'altitudine media della cella.

Il deposito rimane, per il momento, il proxy geografico dell'agenzia.

## Decisioni principali

- PVGIS seleziona il mese tipico e l'anno sorgente per ciascuno dei dodici mesi.
- Open‑Meteo fornisce la temperatura per gli stessi timestamp storici scelti da PVGIS.
- A Open‑Meteo non viene passata l'altitudine PVGIS: `cell_selection=land` e il DEM del modello determinano la quota della cella usata.
- La corrispondenza è diretta, timestamp per timestamp. Non vengono applicati né la media `t/t+1` né il blend tra mesi usati nella generazione EPW di DREAM.
- Il risultato viene rimappato sul calendario sintetico UTC del 2030 e contiene esattamente 8.760 ore.
- Se Open‑Meteo non restituisce una serie completa e valida, la generazione fallisce. Non esiste un fallback silenzioso alla temperatura PVGIS.
- I campi diversi da `temp_air` restano invariati e conservano provenienza PVGIS.
- Le coordinate delle nuove serie sono canonizzate a cinque decimali. Durante il backfill, le coordinate già memorizzate nel database restano autorevoli.

La soluzione deriva dal metodo adottato in [DREAM](https://github.com/supsi-dacd-isaac/dream/blob/main/app/hybrid_tmy.py), ma elimina deliberatamente le trasformazioni necessarie solo alla semantica EPW.

## Flusso di calcolo

1. Il servizio canonizza latitudine e longitudine a cinque decimali.
2. Interroga l'endpoint TMY JSON di PVGIS una sola volta.
3. Valida che PVGIS abbia restituito dodici mesi distinti e 8.760 righe orarie.
4. Per ciascun mese legge l'anno sorgente selezionato da PVGIS.
5. Interroga Open‑Meteo Archive per i dodici intervalli mensili, con al massimo quattro richieste concorrenti.
6. Per ogni timestamp PVGIS cerca il campione Open‑Meteo con lo stesso timestamp UTC.
7. Mantiene i campi PVGIS di umidità, radiazione, vento e pressione, sostituendo soltanto `temp_air`.
8. Rimappa mese, giorno e ora sul 2030 UTC.
9. Valida unicità, continuità, finitezza dei valori e copertura completa di 8.760 ore.
10. Applica la serie in una transazione e rigenera tutte le configurazioni di cluster già esistenti per quella coordinata.

Le richieste Open‑Meteo usano questi parametri:

```text
hourly=temperature_2m
models=best_match
timezone=GMT
cell_selection=land
```

Il parametro `elevation` non viene inviato. Il servizio registra comunque coordinate della griglia ed elevazione restituite da Open‑Meteo, così che il dato applicato sia verificabile in seguito.

## Provenienza dei campi

| Campo | Sorgente | Trattamento |
| --- | --- | --- |
| `time_utc` | PVGIS + Elettra | Timestamp PVGIS rimappato sul 2030 UTC |
| `temp_air` | Open‑Meteo Archive | `temperature_2m` allo stesso timestamp storico PVGIS |
| `relative_humidity` | PVGIS | Invariato |
| `ghi`, `dni`, `dhi`, `IR(h)` | PVGIS | Invariati |
| `wind_speed`, `wind_direction` | PVGIS | Invariati |
| `pressure` | PVGIS | Invariato |

## Robustezza e validazioni

Le chiamate a PVGIS e Open‑Meteo hanno al massimo tre tentativi. I retry sono ammessi per timeout, errori di trasporto, HTTP `429` e HTTP `5xx`; quando presente viene rispettato `Retry-After`, con un limite alla pausa applicata dal servizio.

La serie viene rifiutata se si verifica almeno una delle seguenti condizioni:

- PVGIS non restituisce esattamente dodici mesi o 8.760 righe;
- un timestamp PVGIS non appartiene all'anno scelto per quel mese;
- ci sono timestamp duplicati, mancanti o non continui;
- Open‑Meteo non contiene uno dei timestamp richiesti;
- tempi e temperature Open‑Meteo hanno lunghezze differenti;
- una temperatura è nulla, non finita o fuori dall'intervallo di sicurezza da −90 °C a 70 °C;
- l'anno sintetico è bisestile o non può contenere i timestamp selezionati.

Queste verifiche impediscono che un errore parziale a monte produca una serie apparentemente valida nel database.

## API

La route esistente resta compatibile:

```http
GET /api/v1/simulation/pvgis-tmy/?latitude=<lat>&longitude=<lon>&download=<bool>
```

Con `download=false`, la risposta contiene disponibilità, numero di righe e provenienza. Con `download=true`, restituisce anche le 8.760 misure. In entrambi i casi una serie assente o generata con una versione precedente viene creata e memorizzata.

I metadati aggiunti comprendono:

- `temperature_provider=pvgis-openmeteo`;
- `temperature_model=best_match`;
- `temperature_series_id`;
- `processing_version=elettra-hybrid-temperature-v1`;
- coordinate richieste e canoniche;
- elevazione restituita da Open‑Meteo;
- mesi e anni sorgente selezionati da PVGIS;
- provenienza campo per campo nella risposta completa.

`source=db` indica un cache hit. `source=pvgis-openmeteo` indica che la serie è stata generata e applicata durante quella richiesta.

## Persistenza e tracciabilità

La migrazione `007_add_hybrid_temperature_series.sql` introduce:

- `weather_measurements.temp_air_original`, che conserva una sola volta la temperatura precedente;
- `weather_temperature_series`, che registra coordinate, provider, modello, versione, stato, mesi PVGIS, metadati dei provider, conteggio e date di applicazione o rollback;
- un vincolo che consente una sola serie `applied` per coordinata;
- `temperature_series_id` nelle configurazioni dei cluster;
- il riferimento alla serie e alla configurazione cluster nelle analisi annuali;
- `yearly_analysis_weather_revisions`, che conserva feature precedenti, vecchi e nuovi prediction run, stato ed eventuali errori.

L'applicazione di una coordinata avviene in una singola transazione protetta da advisory lock. `temp_air_original` viene valorizzato solo se è ancora nullo; le 8.760 temperature e tutte le configurazioni cluster esistenti vengono poi aggiornate insieme.

## Backfill controllato

Il comando di backfill separa il download dalla modifica del database:

```bash
python scripts/backfill_hybrid_temperature.py plan \
  --bundle /percorso/hybrid-temperature.json.gz

python scripts/backfill_hybrid_temperature.py apply-weather \
  --bundle /percorso/hybrid-temperature.json.gz \
  --resume

python scripts/backfill_hybrid_temperature.py recalculate-analyses \
  --resume \
  --analysis-map /percorso/analysis-map.json
```

`plan` inventaria dinamicamente tutte le coordinate presenti, scarica e valida i dati, calcola diagnostica prima/dopo e produce un bundle compresso senza modificare il database. Il bundle contiene versione dello schema, provider, modello, versione dell'algoritmo, checksum globale, checksum per serie e checksum della baseline.

`apply-weather` applica esattamente il contenuto revisionato del bundle. Prima di ogni serie ricontrolla i checksum; una serie in errore resta invariata, mentre l'elaborazione continua sulle altre e termina con exit code diverso da zero. `--resume` salta in modo idempotente le serie già applicate con la stessa versione.

`recalculate-analyses` aggiorna le analisi annuali dopo il meteo. Se l'ambiente viene eseguito dall'host Docker, `MINIO_ENDPOINT` deve indicare un endpoint raggiungibile dall'host, per esempio `localhost:9002`; dentro la rete Docker resta normalmente `minio:9000`.

## Associazione di deposito, serie e analisi annuale

Per ogni analisi storica il resolver:

1. determina l'owner dall'optimization run e, in fallback, dai prediction run;
2. preferisce il deposito il cui stop compare negli shift;
3. in assenza usa l'unico deposito dell'owner;
4. in ulteriore fallback usa l'unico deposito dell'agenzia;
5. associa la serie applicata più vicina al deposito;
6. lascia invariata l'analisi se l'associazione rimane ambigua.

I casi ambigui possono essere risolti con una mappa esplicita:

```json
{
  "<yearly-analysis-id>": {
    "latitude": 46.81,
    "longitude": 7.15,
    "k": 8,
    "start_time": "05:00",
    "end_time": "24:00"
  }
}
```

Il ricalcolo conserva l'ottimizzazione di base e la temperatura di sizing. Crea nuovi prediction run non ancora collegati all'analisi, copia tutti gli input e sostituisce soltanto la temperatura con il centroide corretto dello stesso cluster ordinato. Dopo il completamento di tutti i run rigenera `features.scenarios`, `features.results`, `yearlyTotals` ed eventuale `energy_summary`, quindi effettua uno swap atomico. I vecchi run vengono scollegati ma non cancellati.

Se anche un solo nuovo run fallisce, l'analisi precedente rimane interamente attiva.

## Rollback

Il rollback può interessare tutte le serie o una singola coordinata:

```bash
python scripts/backfill_hybrid_temperature.py rollback --all

python scripts/backfill_hybrid_temperature.py rollback \
  --latitude 46.81 \
  --longitude 7.15
```

Prima vengono ripristinate le analisi annuali tramite le revisioni; poi le temperature tornano a `temp_air_original` e i cluster vengono rigenerati. I dati di audit e i prediction run precedenti non vengono eliminati.

## Risultati dell'applicazione del 31 agosto 2026

Nel database di sviluppo usato per l'implementazione sono state corrette 42 serie, pari a 367.920 misure. Tutte le temperature originali sono state conservate; 320 cluster appartenenti a 41 configurazioni sono stati rigenerati. Sono state ricalcolate e collegate 27 analisi annuali, con 216 prediction run sostitutivi completati.

Su tutte le serie del bundle, la correzione media rispetto al PVGIS appena scaricato è stata **+1,485 °C**.

### Smoke test Friburgo / TPF

Per la coordinata storica già presente nel database, `46.81000, 7.15000`:

- Open‑Meteo ha restituito un'elevazione di 638 m;
- media originale nel database: 8,263 °C;
- media ibrida: 9,122 °C;
- variazione media: +0,859 °C;
- un'analisi annuale è stata associata e ricalcolata con questa serie.

La coordinata esatta del deposito provata separatamente, `46.80648, 7.16197`, ha restituito 590 m. Il backfill ha usato correttamente la coordinata storica del database, come previsto dalla regola di non ricampionare implicitamente le serie già esistenti.

L'esecuzione iniziale ha inoltre lasciato a fini di audit sette revisioni fallite e 56 run falliti non collegati, causati da un endpoint MinIO non raggiungibile dall'host. Nessuno di questi run è attivo. La successiva esecuzione con endpoint corretto si è conclusa senza casi irrisolti.

Il container API è stato ricostruito e distribuito il 31 agosto 2026 con l'immagine `elettra-backend:tmy-hybrid-20260831-102be3329b45`. Lo smoke test successivo al deploy ha confermato readiness completa, migrazione 007 e risposta ibrida dalla cache DB.

## Riferimenti nel codice

- Generazione e validazione: [`app/services/hybrid_temperature.py`](../app/services/hybrid_temperature.py)
- Persistenza, cluster e rollback: [`app/services/weather.py`](../app/services/weather.py)
- API compatibile: [`app/routers/simulation.py`](../app/routers/simulation.py)
- Resolver e ricalcolo analisi: [`app/services/yearly_weather_recalculation.py`](../app/services/yearly_weather_recalculation.py)
- CLI di backfill: [`scripts/backfill_hybrid_temperature.py`](../scripts/backfill_hybrid_temperature.py)
- Migrazione: [`db/migrations/007_add_hybrid_temperature_series.sql`](../db/migrations/007_add_hybrid_temperature_series.sql)
- Test del metodo: [`tests/test_hybrid_temperature.py`](../tests/test_hybrid_temperature.py)
- Test bundle e database: [`tests/test_hybrid_temperature_bundle.py`](../tests/test_hybrid_temperature_bundle.py), [`tests/test_hybrid_temperature_db.py`](../tests/test_hybrid_temperature_db.py)

## Versionamento

La versione corrente è `elettra-hybrid-temperature-v1`. Un cambiamento a parametri Open‑Meteo, matching temporale, coordinate, validazioni o logica di rimappatura deve produrre una nuova versione. In questo modo cache, bundle, serie applicate, cluster e analisi restano riproducibili e distinguibili.
