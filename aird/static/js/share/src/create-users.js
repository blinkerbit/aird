import { selectedUsers, selectedModifyUsers, shareVars } from './state.js';
import { escapeHtml, escapeAttr, findCheckboxByValue } from './utils.js';

function toggleUserSelectionPanel() {
  const accessType = document.querySelector('input[name="accessType"]:checked').value;
  const userSelection = document.getElementById('userSelection');

  if (accessType === 'restricted') {
    userSelection.style.display = 'block';
    // Clear previous search and selections
    document.getElementById('userSearchInput').value = '';
    document.getElementById('userList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
    updateSelectedUsersDisplay();
  } else {
    userSelection.style.display = 'none';
    selectedUsers.clear();
    updateSelectedUsersDisplay();
  }
}

function setupUserSearch() {
  const searchInput = document.getElementById('userSearchInput');
  searchInput.addEventListener('input', function () {
    const query = this.value.trim();

    // Clear previous timeout
    if (shareVars.searchTimeout) {
      clearTimeout(shareVars.searchTimeout);
    }

    if (query.length < 1) {
      document.getElementById('userList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
      return;
    }

    // Debounce search requests
    shareVars.searchTimeout = setTimeout(() => {
      searchUsers(query);
    }, 300);
  });
}

async function searchUsers(query) {
  try {
    const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`);
    if (response.ok) {
      const data = await response.json();
      renderUserList(data.users || []);
    } else {
      document.getElementById('userList').innerHTML = '<em class="share-error-text">Error searching users</em>';
    }
  } catch (error) {
    console.error('Error searching users:', error);
    document.getElementById('userList').innerHTML = '<em class="share-error-text">Error searching users</em>';
  }
}

function renderUserList(users) {
  const userList = document.getElementById('userList');

  if (users.length === 0) {
    userList.innerHTML = '<em class="share-hint">No users found</em>';
    return;
  }

  userList.innerHTML = '';
  users.forEach(user => {
    const userDiv = document.createElement('div');
    userDiv.style.marginBottom = '5px';
    const isSelected = selectedUsers.has(user.username);
    const label = document.createElement('label');
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.gap = '8px';
    label.style.cursor = 'pointer';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = user.username;
    checkbox.checked = isSelected;
    checkbox.dataset.action = 'toggleUserSelection';
    checkbox.dataset.user = user.username;

    const span = document.createElement('span');
    span.textContent = `${user.username} ${user.role === 'admin' ? '(Admin)' : ''}`;

    label.appendChild(checkbox);
    label.appendChild(span);
    userDiv.appendChild(label);
    userList.appendChild(userDiv);
  });
}

function toggleUserSelection(username) {
  if (selectedUsers.has(username)) {
    selectedUsers.delete(username);
  } else {
    selectedUsers.add(username);
  }
  updateSelectedUsersDisplay();
}

function updateSelectedUsersDisplay() {
  const selectedUsersList = document.getElementById('selectedUsersList');

  if (selectedUsers.size === 0) {
    selectedUsersList.innerHTML = '<em class="share-hint">No users selected</em>';
    return;
  }

  selectedUsersList.innerHTML = Array.from(selectedUsers)
    .map(username => `<span class="config-tag access" data-action="removeSelectedUser" data-user="${escapeAttr(username)}">${escapeHtml(username)} ×</span>`)
    .join('');
}

function removeSelectedUser(username) {
  selectedUsers.delete(username);
  updateSelectedUsersDisplay();

  // Update checkbox in search results if visible
  const checkbox = findCheckboxByValue(document.getElementById('userList'), username);
  if (checkbox) {
    checkbox.checked = false;
  }
}

function setupModifyUserSearch() {
  const searchInput = document.getElementById('modifyUserSearchInput');
  if (!searchInput) return;
  searchInput.addEventListener('input', function () {
    const query = this.value.trim();
    if (shareVars.modifySearchTimeout) clearTimeout(shareVars.modifySearchTimeout);
    if (query.length < 1) {
      document.getElementById('modifyUserList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
      return;
    }
    shareVars.modifySearchTimeout = setTimeout(() => searchModifyUsers(query), 300);
  });
}

async function searchModifyUsers(query) {
  try {
    const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`);
    if (response.ok) {
      const data = await response.json();
      renderModifyUserList(data.users || []);
    } else {
      document.getElementById('modifyUserList').innerHTML = '<em class="share-error-text">Error searching users</em>';
    }
  } catch (error) {
    console.error('Error searching modify users:', error);
    document.getElementById('modifyUserList').innerHTML = '<em class="share-error-text">Error searching users</em>';
  }
}

function renderModifyUserList(users) {
  const userList = document.getElementById('modifyUserList');
  if (users.length === 0) {
    userList.innerHTML = '<em class="share-hint">No users found</em>';
    return;
  }
  userList.innerHTML = '';
  users.forEach(user => {
    const userDiv = document.createElement('div');
    userDiv.style.marginBottom = '5px';
    const isSelected = selectedModifyUsers.has(user.username);
    const label = document.createElement('label');
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.gap = '8px';
    label.style.cursor = 'pointer';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = user.username;
    checkbox.checked = isSelected;
    checkbox.dataset.action = 'toggleModifyUserSelection';
    checkbox.dataset.user = user.username;
    const span = document.createElement('span');
    span.textContent = `${user.username} ${user.role === 'admin' ? '(Admin)' : ''}`;
    label.appendChild(checkbox);
    label.appendChild(span);
    userDiv.appendChild(label);
    userList.appendChild(userDiv);
  });
}

function toggleModifyUserSelection(username) {
  if (selectedModifyUsers.has(username)) {
    selectedModifyUsers.delete(username);
  } else {
    selectedModifyUsers.add(username);
  }
  updateSelectedModifyUsersDisplay();
}

function updateSelectedModifyUsersDisplay() {
  const list = document.getElementById('selectedModifyUsersList');
  if (selectedModifyUsers.size === 0) {
    list.innerHTML = '<em class="share-hint">No modify users (read-only)</em>';
    return;
  }
  list.innerHTML = Array.from(selectedModifyUsers)
    .map(u => `<span class="config-tag editor" data-action="removeSelectedModifyUser" data-user="${escapeAttr(u)}">✏️ ${escapeHtml(u)} ×</span>`)
    .join('');
}

function removeSelectedModifyUser(username) {
  selectedModifyUsers.delete(username);
  updateSelectedModifyUsersDisplay();
  const checkbox = findCheckboxByValue(document.getElementById('modifyUserList'), username);
  if (checkbox) checkbox.checked = false;
}

export {
  toggleUserSelectionPanel,
  setupUserSearch,
  searchUsers,
  renderUserList,
  toggleUserSelection,
  updateSelectedUsersDisplay,
  removeSelectedUser,
  setupModifyUserSearch,
  searchModifyUsers,
  renderModifyUserList,
  toggleModifyUserSelection,
  updateSelectedModifyUsersDisplay,
  removeSelectedModifyUser,
};
