const form = document.getElementById('verify');
function updateMode() {
  const url = form.elements.mode.value === 'url';
  document.getElementById('prompt').textContent = url ? 'Adres strony do sprawdzenia' : 'Treść do sprawdzenia';
  form.elements.material.placeholder = url ? 'https://…' : 'Wklej wiadomość lub wpisz twierdzenie…';
}
form.addEventListener('change', updateMode);
updateMode();
form.addEventListener('submit', () => {
  form.querySelector('button').disabled = true;
  document.getElementById('progress').textContent = 'Trwa weryfikacja…';
});
document.getElementById('wynik')?.focus();
