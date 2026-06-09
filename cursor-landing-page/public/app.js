// Interactive demo toggle
const toggle = document.getElementById("demo-toggle");
const panel = document.getElementById("demo-panel");

toggle.addEventListener("click", () => {
  const isOpen = !panel.hidden;
  panel.hidden = isOpen;
  toggle.setAttribute("aria-expanded", String(!isOpen));
  toggle.textContent = isOpen ? "Reveal this week's brief" : "Hide this week's brief";

  if (!isOpen) {
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
});

// Signup form
const form = document.getElementById("signup-form");
const emailInput = document.getElementById("email");
const message = document.getElementById("signup-message");
const submitBtn = form.querySelector("button[type='submit']");

function setMessage(text, type) {
  message.textContent = text;
  message.className = "signup-message" + (type ? " " + type : "");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = emailInput.value.trim();

  if (!email) {
    setMessage("Please enter your email address.", "error");
    return;
  }

  submitBtn.disabled = true;
  const originalLabel = submitBtn.textContent;
  submitBtn.textContent = "Joining…";

  try {
    const res = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();

    if (res.ok) {
      setMessage(data.message, "success");
      if (!data.alreadySignedUp) form.reset();
    } else {
      setMessage(data.error || "Something went wrong. Please try again.", "error");
    }
  } catch (err) {
    setMessage("Network error. Please check your connection and try again.", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalLabel;
  }
});
