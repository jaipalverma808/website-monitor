/**
 * Website Monitor Dashboard Interactivity
 * Pure Vanilla JavaScript with zero external dependencies.
 */

function toggleAddModal(show) {
    const modal = document.getElementById('add-modal');
    if (modal) {
        modal.style.display = show ? 'flex' : 'none';
        if (show) {
            const nameInput = document.getElementById('name');
            if (nameInput) nameInput.focus();
        }
    }
}

function toggleEditModal(show) {
    const modal = document.getElementById('edit-modal');
    if (modal) {
        modal.style.display = show ? 'flex' : 'none';
        if (show) {
            const editNameInput = document.getElementById('edit_name');
            if (editNameInput) editNameInput.focus();
        }
    }
}

// Close modals on Escape key or outside click
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        toggleAddModal(false);
        toggleEditModal(false);
    }
});

document.addEventListener('click', function(event) {
    const addModal = document.getElementById('add-modal');
    const editModal = document.getElementById('edit-modal');
    if (event.target === addModal) {
        toggleAddModal(false);
    }
    if (event.target === editModal) {
        toggleEditModal(false);
    }
});
