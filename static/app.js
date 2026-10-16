const calendarGrid = document.querySelector("#calendarGrid");
const calendarRange = document.querySelector("#calendarRange");
const calendarSelection = document.querySelector("#calendarSelection");
const calendarKayakName = document.querySelector("#calendarKayakName");
const previousDates = document.querySelector("#previousDates");
const nextDates = document.querySelector("#nextDates");
const bookingDateInput = document.querySelector("#bookingDate");
const kayakInput = document.querySelector("[name='kayak_id']");
const selectedKayakName = document.querySelector("#selectedKayakName");
const selectedKayakPrice = document.querySelector("#selectedKayakPrice");
const selectedKayakShortcut = document.querySelector("#selectedKayakShortcut");
const kayakCards = document.querySelectorAll(".kayak-card");
const priceSummary = document.querySelector("#priceSummary");
const summaryLine = document.querySelector("#summaryLine");
const summaryTotal = document.querySelector("#summaryTotal");
const bookingBar = document.querySelector("#bookingBar");
const bookingBarPrice = document.querySelector("#bookingBarPrice");
const bookingBarMeta = document.querySelector("#bookingBarMeta");

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const startDateInput = document.querySelector("#startDate");
const endDateInput = document.querySelector("#endDate");

let selectedKayakId = kayakInput?.value || "1";
let selectedStart = startDateInput?.value || null;
let selectedEnd = endDateInput?.value || selectedStart;
let monthAvailability = {};

// The owner can switch multi-day bookings off; a second tap then starts a new selection.
const MULTI_DAY = document.querySelector(".calendar")?.dataset.multiDay !== "0";
const MAX_RANGE_DAYS = MULTI_DAY ? 14 : 1;

function rangeDays(startIso, endIso) {
  const [y1, m1, d1] = startIso.split("-").map(Number);
  const [y2, m2, d2] = endIso.split("-").map(Number);
  return Math.round((new Date(y2, m2 - 1, d2) - new Date(y1, m1 - 1, d1)) / 86400000) + 1;
}

function rangeIsFree(startIso, endIso) {
  // Best effort with the loaded month; the server re-validates everything.
  return Object.entries(monthAvailability).every(([iso, available]) => {
    if (iso <= startIso || iso >= endIso) {
      return true;
    }
    return available;
  });
}

function syncDateInputs() {
  if (startDateInput) {
    startDateInput.value = selectedStart || "";
  }
  if (endDateInput) {
    endDateInput.value = selectedEnd || selectedStart || "";
  }
  if (bookingDateInput) {
    if (!selectedStart) {
      bookingDateInput.value = "";
    } else if (!selectedEnd || selectedEnd === selectedStart) {
      bookingDateInput.value = selectedStart;
    } else {
      bookingDateInput.value = `${selectedStart} to ${selectedEnd}`;
    }
    if (selectedStart) {
      showFieldError(bookingDateInput, "");
    }
  }
}

const today = new Date();
today.setHours(0, 0, 0, 0);
let viewYear = today.getFullYear();
let viewMonth = today.getMonth();

// Local-date ISO string. Never toISOString(): that converts to UTC and can
// shift the date by a day depending on the visitor's timezone.
function isoDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function currentKayakCard() {
  return [...kayakCards].find((card) => card.dataset.kayakId === selectedKayakId);
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

function updateSelectionLine() {
  updateBookingSummary();
  if (!calendarSelection) {
    return;
  }
  if (!selectedStart) {
    calendarSelection.textContent = "No date selected yet — tap a day above.";
    return;
  }
  const card = currentKayakCard();
  const name = card ? card.dataset.kayakName : "Kayak";
  const days = selectedEnd ? rangeDays(selectedStart, selectedEnd) : 1;
  const rate = card ? Number(card.dataset.kayakPrice) : 0;
  const total = discountedTotal(rate * days);
  if (!selectedEnd || selectedEnd === selectedStart) {
    calendarSelection.innerHTML = `<strong>${prettyDate(selectedStart)}</strong> · ${name} · <strong>Rs ${total}</strong>${MULTI_DAY ? ' <em class="selection-hint">Tap another day for a multi-day rental</em>' : ""}`;
  } else {
    calendarSelection.innerHTML = `<strong>${prettyDate(selectedStart)} – ${prettyDate(selectedEnd)}</strong> · ${days} days · ${name} · <strong>Rs ${total}</strong>`;
  }
}

let activePromo = null;

function discountedTotal(rate) {
  if (!activePromo) {
    return rate;
  }
  const discount = activePromo.kind === "percent"
    ? Math.min(rate, Math.round((rate * activePromo.value) / 100))
    : Math.min(rate, activePromo.value);
  return rate - discount;
}

function updateBookingSummary() {
  const card = currentKayakCard();
  const rate = card ? Number(card.dataset.kayakPrice) : 0;
  const days = selectedStart && selectedEnd ? rangeDays(selectedStart, selectedEnd) : 1;
  const total = discountedTotal(rate * days);
  if (bookingBarPrice) {
    bookingBarPrice.textContent = `Rs ${total}`;
  }
  if (selectedStart) {
    if (priceSummary) {
      priceSummary.hidden = false;
      summaryLine.textContent = activePromo
        ? `Rs ${rate} × ${days} day${days > 1 ? "s" : ""} − ${activePromo.code}`
        : `Rs ${rate} × ${days} day${days > 1 ? "s" : ""}`;
      summaryTotal.textContent = `Rs ${total}`;
    }
    if (bookingBarMeta) {
      bookingBarMeta.textContent = selectedEnd && selectedEnd !== selectedStart
        ? `${prettyDate(selectedStart)} – ${prettyDate(selectedEnd)}`
        : prettyDate(selectedStart);
    }
  } else {
    if (priceSummary) {
      priceSummary.hidden = true;
    }
    if (bookingBarMeta) {
      bookingBarMeta.textContent = "Pick a date to book";
    }
  }
}

// Mobile sticky bar: appears past the hero, hides while the form is on screen.
if (bookingBar) {
  const hero = document.querySelector(".hero");
  const bookingSection = document.querySelector("#booking");
  let pastHero = false;
  let formVisible = false;
  const syncBar = () => {
    bookingBar.hidden = !pastHero || formVisible;
  };
  if (hero) {
    new IntersectionObserver(([entry]) => {
      pastHero = !entry.isIntersecting;
      syncBar();
    }).observe(hero);
  }
  if (bookingSection) {
    new IntersectionObserver(([entry]) => {
      formVisible = entry.isIntersecting;
      syncBar();
    }).observe(bookingSection);
  }
}

function renderSkeleton() {
  calendarGrid.innerHTML = "";
  for (let i = 0; i < 35; i += 1) {
    const tile = document.createElement("span");
    tile.className = "day skeleton";
    calendarGrid.appendChild(tile);
  }
}

async function loadAvailability() {
  if (!calendarGrid) {
    return;
  }
  renderSkeleton();

  const firstOfMonth = new Date(viewYear, viewMonth, 1);
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  calendarRange.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;
  previousDates.disabled = viewYear === today.getFullYear() && viewMonth === today.getMonth();

  let days;
  try {
    const response = await fetch(
      `/api/availability?kayak_id=${selectedKayakId}&start=${isoDate(firstOfMonth)}&days=${daysInMonth}`
    );
    if (!response.ok) {
      throw new Error(`availability ${response.status}`);
    }
    days = await response.json();
  } catch (error) {
    calendarGrid.innerHTML = "<p class=\"calendar-error\">Could not load availability. Please refresh the page.</p>";
    return;
  }

  calendarGrid.innerHTML = "";
  monthAvailability = {};

  // Monday-first leading blanks so days align with the weekday header.
  const leading = (firstOfMonth.getDay() + 6) % 7;
  for (let i = 0; i < leading; i += 1) {
    const blank = document.createElement("span");
    blank.className = "day blank";
    calendarGrid.appendChild(blank);
  }

  days.forEach((day) => {
    const [y, m, d] = day.date.split("-").map(Number);
    const dateObj = new Date(y, m - 1, d);
    const isPast = dateObj < today;
    const isToday = day.date === isoDate(today);
    const bookable = day.available && !isPast;

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "day";
    tile.textContent = String(d);
    if (isToday) {
      tile.classList.add("today");
    }
    if (isPast) {
      tile.classList.add("past");
      tile.disabled = true;
    } else if (day.closed) {
      tile.classList.add("closed");
      tile.disabled = true;
    } else if (!day.available) {
      tile.classList.add("booked");
      tile.disabled = true;
    } else {
      tile.classList.add("available");
    }
    monthAvailability[day.date] = day.available && !isPast;
    if (selectedStart && day.date === selectedStart) {
      tile.classList.add("selected");
    }
    if (selectedEnd && day.date === selectedEnd) {
      tile.classList.add("selected");
    }
    if (selectedStart && selectedEnd && day.date > selectedStart && day.date < selectedEnd) {
      tile.classList.add("in-range");
    }
    const prettyFull = dateObj.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    tile.setAttribute(
      "aria-label",
      `${prettyFull} — ${isPast ? "past" : day.closed ? "closed" : day.available ? "available" : "booked"}`
    );
    if (bookable) {
      tile.addEventListener("click", () => {
        if (!selectedStart || (selectedStart && selectedEnd && selectedEnd !== selectedStart) || day.date < selectedStart) {
          // Start a fresh selection
          selectedStart = day.date;
          selectedEnd = day.date;
        } else if (day.date === selectedStart) {
          selectedEnd = day.date;
        } else {
          // Extend to a range if every day between is free and within the cap
          if (rangeDays(selectedStart, day.date) > MAX_RANGE_DAYS) {
            selectedStart = day.date;
            selectedEnd = day.date;
          } else if (rangeIsFree(selectedStart, day.date)) {
            selectedEnd = day.date;
          } else {
            selectedStart = day.date;
            selectedEnd = day.date;
          }
        }
        syncDateInputs();
        // Re-render highlights without refetching
        calendarGrid.querySelectorAll(".day").forEach((other) => other.classList.remove("selected", "in-range"));
        calendarGrid.querySelectorAll(".day").forEach((other) => {
          const label = other.getAttribute("data-date");
          if (!label) {
            return;
          }
          if (label === selectedStart || label === selectedEnd) {
            other.classList.add("selected");
          } else if (selectedStart && selectedEnd && label > selectedStart && label < selectedEnd) {
            other.classList.add("in-range");
          }
        });
        updateSelectionLine();
      });
    }
    tile.setAttribute("data-date", day.date);
    calendarGrid.appendChild(tile);
  });

  updateSelectionLine();
}

function selectKayak(card, options = {}) {
  const changed = selectedKayakId !== card.dataset.kayakId;
  selectedKayakId = card.dataset.kayakId;
  kayakInput.value = selectedKayakId;
  selectedKayakName.textContent = card.dataset.kayakName;
  selectedKayakPrice.textContent = `Rs ${card.dataset.kayakPrice}/day`;
  if (calendarKayakName) {
    calendarKayakName.textContent = card.dataset.kayakName;
  }
  if (changed && !options.keepDate) {
    selectedStart = null;
    selectedEnd = null;
    syncDateInputs();
  }
  updateBookingSummary();

  kayakCards.forEach((kayakCard) => {
    const isSelected = kayakCard === card;
    kayakCard.classList.toggle("selected", isSelected);
    kayakCard.setAttribute("aria-pressed", String(isSelected));
  });

  loadAvailability();
  if (options.scroll !== false) {
    document.querySelector("#availability")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

previousDates?.addEventListener("click", () => {
  viewMonth -= 1;
  if (viewMonth < 0) {
    viewMonth = 11;
    viewYear -= 1;
  }
  loadAvailability();
});

nextDates?.addEventListener("click", () => {
  viewMonth += 1;
  if (viewMonth > 11) {
    viewMonth = 0;
    viewYear += 1;
  }
  loadAvailability();
});

kayakCards.forEach((card) => {
  card.addEventListener("click", () => selectKayak(card));
});

selectedKayakShortcut?.addEventListener("click", () => {
  document.querySelector("#fleet")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

// The date field is filled from the calendar; clicking it takes you there.
bookingDateInput?.addEventListener("click", () => {
  document.querySelector("#availability")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

// Inline validation: message appears when you leave a field, clears as you fix it.
const fieldValidators = {
  customer_name: (value) => (value.trim() ? "" : "Please enter your full name."),
  customer_email: (value) => (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()) ? "" : "Please enter a valid email address."),
  booking_date: () => (startDateInput?.value ? "" : "Please pick your dates from the calendar above."),
};

function showFieldError(input, message) {
  let error = input.parentElement.querySelector(".field-error");
  if (!message) {
    input.removeAttribute("aria-invalid");
    error?.remove();
    return;
  }
  if (!error) {
    error = document.createElement("span");
    error.className = "field-error";
    error.id = `error-${input.name.replace("_", "-")}`;
    input.parentElement.appendChild(error);
  }
  error.textContent = message;
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", error.id);
}

document.querySelectorAll(".booking-form [name]").forEach((input) => {
  const validate = fieldValidators[input.name];
  if (!validate) {
    return;
  }
  input.addEventListener("blur", () => showFieldError(input, validate(input.value)));
  input.addEventListener("input", () => {
    if (input.getAttribute("aria-invalid")) {
      showFieldError(input, validate(input.value));
    }
  });
});

window.addEventListener("load", () => {
  // After a server-side validation error, restore the selected kayak and
  // bring the visitor back to the form instead of dumping them at the top.
  const serverError = document.querySelector(".field-error");
  const initialCard = [...kayakCards].find((card) => card.dataset.kayakId === kayakInput?.value);
  if (calendarKayakName && initialCard) {
    calendarKayakName.textContent = initialCard.dataset.kayakName;
  }
  if (initialCard && !initialCard.classList.contains("selected")) {
    selectKayak(initialCard, { scroll: false, keepDate: true });
  } else {
    loadAvailability();
  }
  if (serverError) {
    document.querySelector("#booking")?.scrollIntoView({ block: "start" });
  }
});

function dismissToast(toast) {
  toast.classList.add("is-leaving");
  setTimeout(() => toast.remove(), 350);
}

document.querySelectorAll(".toast").forEach((toast) => {
  toast.querySelector("[data-toast-close]")?.addEventListener("click", () => dismissToast(toast));
  if (!toast.hasAttribute("data-persistent")) {
    setTimeout(() => dismissToast(toast), 6000);
  }
});


// --- Two-step checkout: details -> review -> confirm ----------------------

const formProgress = document.querySelector("#formProgress");
const stepDetails = document.querySelector("#formStepDetails");
const stepReview = document.querySelector("#formStepReview");
const reviewButton = document.querySelector("#reviewButton");
const submitDirect = document.querySelector("#submitDirect");
const backToDetails = document.querySelector("#backToDetails");

function setStep(step) {
  const inReview = step === 2;
  stepDetails.hidden = inReview;
  stepReview.hidden = !inReview;
  formProgress.querySelectorAll(".progress-dot").forEach((dot) => {
    dot.classList.toggle("is-active", Number(dot.dataset.step) <= step);
  });
  if (inReview) {
    stepReview.scrollIntoView({ behavior: "smooth", block: "center" });
    stepReview.querySelector("button")?.focus({ preventScroll: true });
  }
}

function fillReview() {
  const form = document.querySelector(".booking-form");
  const card = currentKayakCard();
  const days = selectedStart && selectedEnd ? rangeDays(selectedStart, selectedEnd) : 1;
  const rate = card ? discountedTotal(Number(card.dataset.kayakPrice) * days) : 0;
  document.querySelector("#reviewKayak").textContent = card ? card.dataset.kayakName : "";
  document.querySelector("#reviewDate").textContent = !selectedStart
    ? ""
    : selectedEnd && selectedEnd !== selectedStart
      ? `${prettyDate(selectedStart)} – ${prettyDate(selectedEnd)} (${days} days)`
      : prettyDate(selectedStart);
  document.querySelector("#reviewName").textContent = form.customer_name.value.trim();
  document.querySelector("#reviewEmail").textContent = form.customer_email.value.trim();
  document.querySelector("#reviewPhone").textContent = form.customer_phone.value.trim() || "Not provided";
  document.querySelector("#reviewTotal").textContent = `Rs ${rate}`;
}

if (reviewButton && stepDetails && stepReview) {
  // JS available: enhance into the two-step flow.
  reviewButton.hidden = false;
  submitDirect.hidden = true;
  formProgress.hidden = false;

  reviewButton.addEventListener("click", () => {
    let firstInvalid = null;
    Object.entries(fieldValidators).forEach(([name, validate]) => {
      const input = document.querySelector(`.booking-form [name='${name}']`);
      if (!input) {
        return;
      }
      const message = validate(input.value);
      showFieldError(input, message);
      if (message && !firstInvalid) {
        firstInvalid = input;
      }
    });
    if (firstInvalid) {
      firstInvalid.focus();
      return;
    }
    fillReview();
    setStep(2);
  });

  backToDetails.addEventListener("click", () => setStep(1));
}

// One click only: disable the submit button once the booking is on its way.
document.querySelector(".booking-form")?.addEventListener("submit", (event) => {
  const button = event.submitter;
  if (button) {
    button.disabled = true;
    button.textContent = "Booking…";
  }
});


// Copy-to-clipboard chips (confirmation page reference)
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.classList.add("copied");
      setTimeout(() => button.classList.remove("copied"), 1200);
    } catch (error) {
      /* clipboard unavailable (http, old browser) — reference is visible anyway */
    }
  });
});


// Conflict recovery: tapping a suggested date fills it in and clears the error.
document.querySelectorAll(".date-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    selectedStart = chip.dataset.date;
    selectedEnd = chip.dataset.date;
    syncDateInputs();
    document.querySelector(".date-suggestions")?.remove();
    updateBookingSummary();
    updateSelectionLine();
    loadAvailability();
  });
});


// Promo code live check
const promoInput = document.querySelector("#promoCode");
const promoStatus = document.querySelector("#promoStatus");

promoInput?.addEventListener("blur", async () => {
  const code = promoInput.value.trim();
  if (!code) {
    activePromo = null;
    promoStatus.hidden = true;
    updateBookingSummary();
    return;
  }
  try {
    const response = await fetch(`/api/promo?code=${encodeURIComponent(code)}`);
    const data = await response.json();
    promoStatus.hidden = false;
    if (data.valid) {
      activePromo = data;
      promoStatus.textContent = data.kind === "percent"
        ? `✓ ${data.code}: ${data.value}% off`
        : `✓ ${data.code}: Rs ${data.value} off`;
      promoStatus.className = "promo-status is-valid";
    } else {
      activePromo = null;
      promoStatus.textContent = data.error;
      promoStatus.className = "promo-status is-invalid";
    }
  } catch (error) {
    activePromo = null;
    promoStatus.hidden = true;
  }
  updateBookingSummary();
});
