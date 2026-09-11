export function formatCurrency(value, currency = null) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof window !== "undefined" && window.frappe?.format) {
    try {
      return window.frappe.format(value, { fieldtype: "Currency", currency });
    } catch (e) {
      // fallback
    }
  }
  const num = Number(value);
  if (isNaN(num)) return String(value);
  return num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatFloat(value, precision = 2) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof window !== "undefined" && window.frappe?.format) {
    try {
      return window.frappe.format(value, { fieldtype: "Float", precision });
    } catch (e) {
      // fallback
    }
  }
  const num = Number(value);
  if (isNaN(num)) return String(value);
  return num.toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

export function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof window !== "undefined" && window.frappe?.format) {
    try {
      return window.frappe.format(value, { fieldtype: "Int" });
    } catch (e) {
      // fallback
    }
  }
  const num = Number(value);
  if (isNaN(num)) return String(value);
  return num.toLocaleString();
}

export function formatRelativeTime(dateStr) {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return String(dateStr);
  
  const now = new Date();
  const diffSeconds = Math.floor((now - date) / 1000);
  
  if (diffSeconds < 60) return "Just now";
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
  if (diffSeconds < 172800) return "Yesterday";
  if (diffSeconds < 604800) return `${Math.floor(diffSeconds / 86400)}d ago`;
  
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function groupConversationsByDate(conversations) {
  const groups = {
    Today: [],
    Yesterday: [],
    Earlier: [],
  };
  
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 86400000;
  
  for (const conv of conversations) {
    const itemTime = conv.modified ? new Date(conv.modified).getTime() : Date.now();
    if (itemTime >= todayStart) {
      groups.Today.push(conv);
    } else if (itemTime >= yesterdayStart) {
      groups.Yesterday.push(conv);
    } else {
      groups.Earlier.push(conv);
    }
  }
  
  return groups;
}

export function formatBytes(bytes, decimals = 1) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}
