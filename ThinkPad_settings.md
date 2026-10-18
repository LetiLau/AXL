# Gestione Interfaccia Grafica (GNOME/GDM3) su Debian 12

In `systemd`, l'avvio della sessione grafica e dei relativi demoni (GDM, Mutter, PipeWire, sessioni utente) è regolato dai **target** di sistema: `multi-user.target` (equivalente al runlevel 3, solo console TTY e networking) e `graphical.target` (runlevel 5, server grafico e display manager attivi).

---

## 1. Configurazione Persistente (Scelta del Target al Boot)

Definisce se il ThinkPad deve accendersi direttamente in modalità headless (TTY) o con l'interfaccia grafica GNOME attiva.

### Avvio predefinito in modalità Headless (CLI / Consumo minimo)

Disabilita l'avvio automatico del Display Manager al reboot:

```bash
sudo systemctl set-default multi-user.target

```

* **Verifica:** Esegui `systemctl get-default`. L'output deve restituire `multi-user.target`.

### Ripristino avvio predefinito con GNOME

Riattiva l'ambiente grafico come stato di default al boot:

```bash
sudo systemctl set-default graphical.target

```

* **Verifica:** Esegui `systemctl get-default`. L'output deve restituire `graphical.target`.

---

## 2. Switch On-Demand a Runtime (Senza Riavvio)

Permette di avviare o arrestare completamente GNOME e i suoi sottoprocessi durante una sessione attiva.

### Arrestare GNOME e liberare la RAM

Per spegnere il Display Manager e tutte le applicazioni grafiche in esecuzione:

```bash
sudo systemctl isolate multi-user.target

```

*In alternativa, se vuoi fermare solo il display manager senza isolare il target:*

```bash
sudo systemctl stop gdm3

```

* **Verifica:** Esegui `systemctl is-active gdm3`. L'output deve essere `inactive`. Il portatile passerà alla console TTY (o visualizzerà un cursore lampeggiante; premi `Ctrl + Alt + F2` o `F3` se necessario per il login testuale).

### Avviare GNOME on-demand

Quando serve temporaneamente lo schermo, il browser o un tool GUI:

```bash
sudo systemctl isolate graphical.target

```

*In alternativa:*

```bash
sudo systemctl start gdm3

```

* **Verifica:** Il display si riaccenderà mostrando la schermata di login GDM (generalmente su TTY1 o TTY2). Da terminale puoi verificare con `systemctl is-active gdm3`, che deve restituire `active`.

---

## 3. Monitoraggio e Verifica Risorse

Per quantificare il risparmio effettivo di risorse una volta disattivata l'interfaccia grafica:

### Controllo memoria RAM

```bash
free -h

```

* In `multi-user.target` la voce `used` scende tipicamente tra **150 MB e 280 MB**.
* Con GNOME e tracker attivi, la voce `used` si attesta solitamente tra **1.5 GB e 3.0 GB**.

### Verifica processi residui

Se dopo l'arresto noti carichi anomali, controlla che i demoni di indicizzazione GNOME non siano rimasti orfani:

```bash
ps aux | grep -E 'gnome|tracker|gdm'

```

---

## 4. Scorciatoie da Terminale (`.bashrc`)

Per evitare di digitare i comandi estesi di `systemd`, aggiungi questi alias al tuo `~/.bashrc`:

```bash
# Apri la configurazione
nano ~/.bashrc

```

Incolla in fondo al file:

```bash
# Gestione rapida GUI A-X-L Master
alias gui-start='sudo systemctl isolate graphical.target'
alias gui-stop='sudo systemctl isolate multi-user.target'
alias gui-status='systemctl status gdm3 --lines=0 && free -h'

```

Ricarica il file:

```bash
source ~/.bashrc

```

* **`gui-start`**: Avvia GNOME all'istante.
* **`gui-stop`**: Killa la sessione grafica e recupera la memoria RAM per il Master.
* **`gui-status`**: Mostra se GDM è attivo e la RAM attualmente impegnata.
