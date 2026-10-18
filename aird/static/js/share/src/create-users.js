import { selectedUsers, selectedModifyUsers, shareVars } from './state.js';
import { escapeHtml, escapeAttr, findCheckboxByValue, getJson } from './utils.js';

/**
 * One user-search picker. Viewers and editors used to each carry a copy of
 * the debounce, the result list, and the selected-tag row.
 */
function createUserPicker({
  selected,
  searchInputId,
  listId,
  selectedListId,
  timeoutKey,
  toggleAction,
  emptySelectedHtml,
  renderTag,
}) {
  function listEl() {
    return document.getElementById(listId);
  }

  function setupSearch() {
    const searchInput = document.getElementById(searchInputId);
    if (!searchInput) return;
    searchInput.addEventListener('input', function () {
      const query = this.value.trim();
      if (shareVars[timeoutKey]) clearTimeout(shareVars[timeoutKey]);
      if (query.length < 1) {
        listEl().innerHTML = '<em class="share-hint">Type to search for users...</em>';
        return;
      }
      shareVars[timeoutKey] = setTimeout(() => { void search(query); }, 300);
    });
  }

  async function search(query) {
    try {
      const data = await getJson(`/api/users/search?q=${encodeURIComponent(query)}`);
      renderList(data.users || []);
    } catch (error) {
      console.error('Error searching users:', error);
      listEl().innerHTML = '<em class="share-error-text">Error searching users</em>';
    }
  }

  function renderList(users) {
    const userList = listEl();
    if (users.length === 0) {
      userList.innerHTML = '<em class="share-hint">No users found</em>';
      return;
    }
    userList.replaceChildren();
    for (const user of users) {
      const userDiv = document.createElement('div');
      userDiv.style.marginBottom = '5px';
      const label = document.createElement('label');
      label.style.display = 'flex';
      label.style.alignItems = 'center';
      label.style.gap = '8px';
      label.style.cursor = 'pointer';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = user.username;
      checkbox.checked = selected.has(user.username);
      checkbox.dataset.action = toggleAction;
      checkbox.dataset.user = user.username;

      const span = document.createElement('span');
      const role = user.role === 'admin' ? ' (Admin)' : '';
      span.textContent = `${user.username}${role}`;
      label.append(checkbox, span);
      userDiv.appendChild(label);
      userList.appendChild(userDiv);
    }
  }

  function toggle(username) {
    if (selected.has(username)) selected.delete(username);
    else selected.add(username);
    updateSelected();
  }

  function updateSelected() {
    const selectedList = document.getElementById(selectedListId);
    if (selected.size === 0) {
      selectedList.innerHTML = emptySelectedHtml;
      return;
    }
    selectedList.innerHTML = Array.from(selected).map(renderTag).join('');
  }

  function remove(username) {
    selected.delete(username);
    updateSelected();
    const checkbox = findCheckboxByValue(listEl(), username);
    if (checkbox) checkbox.checked = false;
  }

  return { setupSearch, search, renderList, toggle, updateSelected, remove };
}

function userTag(username, action, className, prefix) {
  return `<span class="config-tag ${className}" data-action="${action}" data-user="${escapeAttr(username)}">${prefix}${escapeHtml(username)} ×</span>`;
}

const viewers = createUserPicker({
  selected: selectedUsers,
  searchInputId: 'userSearchInput',
  listId: 'userList',
  selectedListId: 'selectedUsersList',
  timeoutKey: 'searchTimeout',
  toggleAction: 'toggleUserSelection',
  emptySelectedHtml: '<em class="share-hint">No users selected</em>',
  renderTag: (username) => userTag(username, 'removeSelectedUser', 'access', ''),
});

const editors = createUserPicker({
  selected: selectedModifyUsers,
  searchInputId: 'modifyUserSearchInput',
  listId: 'modifyUserList',
  selectedListId: 'selectedModifyUsersList',
  timeoutKey: 'modifySearchTimeout',
  toggleAction: 'toggleModifyUserSelection',
  emptySelectedHtml: '<em class="share-hint">No modify users (read-only)</em>',
  renderTag: (username) => userTag(username, 'removeSelectedModifyUser', 'editor', '✏️ '),
});

function toggleUserSelectionPanel() {
  const accessType = document.querySelector('input[name="accessType"]:checked').value;
  const userSelection = document.getElementById('userSelection');

  if (accessType === 'restricted') {
    userSelection.style.display = 'block';
    document.getElementById('userSearchInput').value = '';
    document.getElementById('userList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
    viewers.updateSelected();
  } else {
    userSelection.style.display = 'none';
    selectedUsers.clear();
    viewers.updateSelected();
  }
}

const setupUserSearch = viewers.setupSearch;
const searchUsers = viewers.search;
const renderUserList = viewers.renderList;
const toggleUserSelection = viewers.toggle;
const updateSelectedUsersDisplay = viewers.updateSelected;
const removeSelectedUser = viewers.remove;

const setupModifyUserSearch = editors.setupSearch;
const searchModifyUsers = editors.search;
const renderModifyUserList = editors.renderList;
const toggleModifyUserSelection = editors.toggle;
const updateSelectedModifyUsersDisplay = editors.updateSelected;
const removeSelectedModifyUser = editors.remove;

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
