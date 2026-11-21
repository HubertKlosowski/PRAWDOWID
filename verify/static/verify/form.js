const form = document.getElementById('verify');
const submitButton = form.querySelector('button[type="submit"]');
const material = form.elements.material;
const urlHint = document.getElementById('url-hint');
const progress = document.getElementById('progress');
const urlStep = document.getElementById('progress-url');
const statusMessage = document.getElementById('verification-status');
const result = document.getElementById('wynik');

function isStandaloneUrl() {
  return /^https?:\/\/\S*$/i.test(material.value.trim());
}

function updateUrlHint() {
  urlHint.hidden = submitButton.disabled || !isStandaloneUrl();
}

function setPending(pending) {
  submitButton.disabled = pending;
  submitButton.textContent = pending ? 'Sprawdzanie…' : 'Sprawdź informację';
  progress.hidden = !pending;
  urlStep.hidden = !pending || !isStandaloneUrl();
  updateUrlHint();
  statusMessage.textContent = pending ? 'Trwa weryfikacja. Wynik pojawi się po zakończeniu analizy.' : '';
  if (result) result.hidden = pending;
}

material.addEventListener('input', updateUrlHint);
form.addEventListener('submit', (event) => {
  if (submitButton.disabled) {
    event.preventDefault();
    return;
  }
  setPending(true);
});
window.addEventListener('pageshow', () => {
  setPending(false);
});

updateUrlHint();
result?.focus();
