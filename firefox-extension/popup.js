document.querySelectorAll('button').forEach(button => {
  button.addEventListener('click', async () => {
    const status = document.getElementById('status');
    try {
      button.disabled = true;
      await browser.runtime.sendMessage({action: button.dataset.action});
      status.textContent = 'Sent to Runespeak Lite.';
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      button.disabled = false;
    }
  });
});
