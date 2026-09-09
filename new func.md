A-X-L has a dynamic voice recognition
I can tell him if he can trust anybody or just the persons he has "saved" in the memory (like me, family and friends)

-   **Friendly Mode:** Ignora il calcolo della distanza. Basta che il trigger (la wake word) superi la soglia di confidenza.
    
-   **Paranoid Mode:** Trigger richiesto + Distanza Coseno rigorosamente inferiore a 0.2 dal _tuo_ embedding (Root User).
    
-   **Restricted Mode:** Trigger richiesto + Distanza Coseno inferiore a 0.3 da un qualsiasi embedding nel file `trusted_users.bin`.