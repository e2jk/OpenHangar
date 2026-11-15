# Security fix: expense receipts and sensitive documents

- **Security:** expense receipts are now shown to owners and admins only, like the expenses themselves since v3.48.0: they are stored with the aircraft's documents, so pilots, maintenance users and viewers could still open them there, and a full-access share page listed their titles. Sensitive documents are now hidden from viewers, as the user guide says; before, the documents list showed them to anyone with "Show sensitive", and their files opened for anyone assigned to the aircraft.

**Maintenance**
- Signed-in security scan: the weekly scan now also uses the browser-based crawler, which reaches the offline data endpoints and the pages the aircraft page fetches in the background (its trial found the expense pages fixed in v3.48.0).
