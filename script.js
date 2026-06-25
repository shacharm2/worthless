document.addEventListener('DOMContentLoaded', function() {
    const elements = document.querySelectorAll('.fade-in');

    // Stagger the animation slightly
    elements.forEach((el, index) => {
        setTimeout(() => {
            el.style.opacity = 1;
        }, (index + 1) * 500); // 500ms delay between h1 and p
    });
});
