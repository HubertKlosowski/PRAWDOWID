const form = document.getElementById('verify');
const submitButton = form.querySelector('button[type="submit"]');
const progress = document.getElementById('progress');
const urlStep = document.getElementById('progress-url');
const statusMessage = document.getElementById('verification-status');
const result = document.getElementById('wynik');

function updateMode() {
  const url = form.elements.mode.value === 'url';
  document.getElementById('prompt').textContent = url ? 'Adres strony do sprawdzenia' : 'Treść do sprawdzenia';
  form.elements.material.placeholder = url ? 'https://…' : 'Wklej wiadomość lub wpisz twierdzenie…';
}

function setPending(pending) {
  submitButton.disabled = pending;
  submitButton.textContent = pending ? 'Sprawdzanie…' : 'Sprawdź informację';
  progress.hidden = !pending;
  urlStep.hidden = form.elements.mode.value !== 'url';
  statusMessage.textContent = pending ? 'Trwa weryfikacja. Wynik pojawi się po zakończeniu analizy.' : '';
  if (result) result.hidden = pending;
}

form.addEventListener('change', updateMode);
form.addEventListener('submit', (event) => {
  if (submitButton.disabled) {
    event.preventDefault();
    return;
  }
  setPending(true);
});
window.addEventListener('pageshow', () => {
  setPending(false);
  updateMode();
});

updateMode();
result?.focus();
