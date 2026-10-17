import { createContext, useContext, useState, useCallback } from 'react'
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react'

const ToastContext = createContext(null)

const TOAST_TYPES = {
  success: {
    icon: CheckCircle,
    bgColor: 'bg-green-50',
    borderColor: 'border-green-200',
    iconColor: 'text-green-500',
    textColor: 'text-green-800',
  },
  error: {
    icon: AlertCircle,
    bgColor: 'bg-red-50',
    borderColor: 'border-red-200',
    iconColor: 'text-red-500',
    textColor: 'text-red-800',
  },
  warning: {
    icon: AlertTriangle,
    bgColor: 'bg-yellow-50',
    borderColor: 'border-yellow-200',
    iconColor: 'text-yellow-500',
    textColor: 'text-yellow-800',
  },
  info: {
    icon: Info,
    bgColor: 'bg-blue-50',
    borderColor: 'border-blue-200',
    iconColor: 'text-blue-500',
    textColor: 'text-blue-800',
  },
}

function Toast({ id, type, message, onClose, action }) {
  const config = TOAST_TYPES[type] || TOAST_TYPES.info
  const Icon = config.icon

  return (
    <div
      className={`flex items-center gap-3 p-4 rounded-lg border shadow-lg ${config.bgColor} ${config.borderColor} animate-slide-in`}
      role="alert"
    >
      <Icon className={`h-5 w-5 flex-shrink-0 ${config.iconColor}`} />
      <p className={`flex-1 text-sm font-medium ${config.textColor}`}>{message}</p>
      {action && (
        <button
          onClick={() => {
            action.onClick()
            onClose(id)
          }}
          className={`text-sm font-semibold ${config.iconColor} hover:underline`}
        >
          {action.label}
        </button>
      )}
      <button
        onClick={() => onClose(id)}
        className={`p-1 rounded hover:bg-black/5 ${config.textColor}`}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback(({ type = 'info', message, duration = 4000, action }) => {
    const id = Date.now() + Math.random()

    setToasts((prev) => [...prev, { id, type, message, action }])

    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, duration)
    }

    return id
  }, [])

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = {
    success: (message, options) => addToast({ type: 'success', message, ...options }),
    error: (message, options) => addToast({ type: 'error', message, ...options }),
    warning: (message, options) => addToast({ type: 'warning', message, ...options }),
    info: (message, options) => addToast({ type: 'info', message, ...options }),
  }

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md">
        {toasts.map((t) => (
          <Toast
            key={t.id}
            id={t.id}
            type={t.type}
            message={t.message}
            action={t.action}
            onClose={removeToast}
          />
        ))}
      </div>
      <style>{`
        @keyframes slide-in {
          from {
            transform: translateX(100%);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        .animate-slide-in {
          animation: slide-in 0.2s ease-out;
        }
      `}</style>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return context
}
