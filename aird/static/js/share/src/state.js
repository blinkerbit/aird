export const shareVars = {
  currentPath: '',
  allFiles: [],
  filePickerLoaded: false,
  currentShareData: null,
  searchTimeout: null,
  modifySearchTimeout: null,
};

export const selectedFiles = new Set();
export const selectedUsers = new Set();
export const selectedModifyUsers = new Set();

export const elements = {
  currentPath: document.getElementById('currentPath'),
  selectedCount: document.getElementById('selectedCount'),
  selectedFilesDiv: document.getElementById('selectedFiles'),
  generateLink: document.getElementById('generateLink'),
  clearSelection: document.getElementById('clearSelection'),
  selectAllVisible: document.getElementById('selectAllVisible'),
  shareResult: document.getElementById('shareResult'),
  fileTableBody: document.getElementById('fileTableBody'),
  sharesTableBody: document.getElementById('sharesTableBody'),
  activeSharesCount: document.getElementById('activeSharesCount'),
  sharedWithMeSection: document.getElementById('sharedWithMeSection'),
  sharedWithMeTableBody: document.getElementById('sharedWithMeTableBody'),
  sharedWithMeCount: document.getElementById('sharedWithMeCount')
};

export const selectedFileMetadata = new Map();

export const cloudElements = {
  modal: document.getElementById('cloudBrowserModal'),
  providerSelect: document.getElementById('cloudProviderSelect'),
  pathDisplay: document.getElementById('cloudPathDisplay'),
  statusMessage: document.getElementById('cloudStatusMessage'),
  tableBody: document.getElementById('cloudFilesTableBody'),
  upButton: document.getElementById('cloudUpButton'),
  uploadInput: document.getElementById('cloudUploadInput'),
  uploadButton: document.getElementById('cloudUploadButton'),
  uploadStatus: document.getElementById('cloudUploadStatus')
};

export const cloudState = {
  providers: [],
  currentProvider: null,
  currentFolder: null,
  currentFiles: [],
  pathStack: [],
  loading: false,
  uploading: false
};

export const addFilesModalData = {
  currentPath: '',
  selectedFiles: new Set(),
  allFiles: []
};
