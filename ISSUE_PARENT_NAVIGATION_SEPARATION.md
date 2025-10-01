# Issue: Separate Parent Folder Navigation from File List

## Problem Description

Currently, the parent directory navigation ("..") is included as a row within the file table in the browse.html template. This creates several UX issues:

1. **Visual Confusion**: The parent directory appears as just another file/folder in the list
2. **Sorting Issues**: The parent directory gets mixed in with actual files when sorting
3. **Inconsistent Behavior**: The parent directory has different styling and actions than regular files
4. **Mobile Responsiveness**: On mobile, the parent directory takes up valuable space in the file list

## Current Implementation

In `aird/templates/browse.html` (lines 609-621), the parent directory is rendered as a table row:

```html
<!-- Parent Directory Link -->
{% if current_path and current_path != "/" %}
<tr>
  <td class="name-cell" data-label="Name">
    <a href="/files/{{ parent_path or '' }}" class="file-link">
      <span class="file-icon">📁</span>..
    </a>
  </td>
  <td class="size-cell" data-label="Size">-</td>
  <td class="modified-cell" data-label="Modified">-</td>
  <td class="actions-cell" data-label="Actions">-</td>
</tr>
{% end %}
```

## Proposed Solution

### 1. Create a Dedicated Navigation Section

Add a separate navigation section above the file table:

```html
<!-- Navigation Section -->
{% if current_path and current_path != "/" %}
<div class="navigation-section">
  <div class="parent-directory">
    <a href="/files/{{ parent_path or '' }}" class="parent-link">
      <span class="nav-icon">⬆️</span> Go to Parent Directory
    </a>
  </div>
</div>
{% end %}
```

### 2. Update CSS Styling

Add dedicated styles for the navigation section:

```css
.navigation-section {
  margin-bottom: 15px;
  padding: 10px;
  background: #f8f9fa;
  border: 1px solid #ddd;
  border-radius: 4px;
}

.parent-directory {
  display: flex;
  align-items: center;
}

.parent-link {
  color: #0066cc;
  text-decoration: none;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
}

.parent-link:hover {
  text-decoration: underline;
  color: #004499;
}

.nav-icon {
  font-size: 16px;
}
```

### 3. Update JavaScript

Remove the parent directory logic from the sorting function:

```javascript
function calculateSkipRows(allRows) {
  // Remove parent directory logic since it's no longer in the table
  // Only skip empty directory message
  if (
    allRows.length > 0 &&
    allRows[0].children.length === 1 &&
    allRows[0].children[0].textContent.includes("This directory is empty")
  ) {
    return 1;
  }
  return 0;
}
```

## Benefits

1. **Clear Visual Separation**: Parent navigation is clearly distinguished from file content
2. **Better Sorting**: File sorting no longer includes navigation elements
3. **Improved Mobile Experience**: Navigation doesn't compete with file list space
4. **Consistent UX**: Navigation follows standard file browser patterns
5. **Accessibility**: Screen readers can better distinguish navigation from content

## Implementation Steps

1. **Phase 1**: Add navigation section above file table
2. **Phase 2**: Remove parent directory from table rows
3. **Phase 3**: Update CSS for new navigation styling
4. **Phase 4**: Update JavaScript sorting logic
5. **Phase 5**: Test responsive design and accessibility

## Files to Modify

- `aird/templates/browse.html` - Main template changes
- CSS styles within the template
- JavaScript sorting functions

## Testing Requirements

- [ ] Parent navigation works correctly
- [ ] File sorting excludes navigation elements
- [ ] Mobile responsive design works
- [ ] Accessibility compliance
- [ ] Visual consistency across different themes

## Priority

**Medium** - This is a UX improvement that enhances usability but doesn't break existing functionality.

## Labels

- `enhancement`
- `ux-improvement`
- `frontend`
- `navigation`
