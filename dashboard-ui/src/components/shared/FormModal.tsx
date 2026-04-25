import { useState, type ReactNode } from 'react';
import { Modal } from 'antd';
import type { FormInstance } from 'antd';

interface FormModalProps {
  title: string;
  open: boolean;
  onCancel: () => void;
  /** Called with validated form values. Throw to keep the modal open. */
  onSubmit: (values: any) => Promise<void>;
  okText?: string;
  children: ReactNode;
  form: FormInstance;
}

/**
 * Modal + Form wrapper used across the dashboard.
 * Handles validate → submit → loading state automatically.
 * Caller is responsible for form layout and success notifications.
 */
export default function FormModal({
  title,
  open,
  onCancel,
  onSubmit,
  okText = 'Submit',
  children,
  form,
}: FormModalProps) {
  const [submitting, setSubmitting] = useState(false);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await onSubmit(values);
    } catch {
      // Validation errors are shown inline; submit errors handled by caller / global interceptor
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={title}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={submitting}
      okText={okText}
      destroyOnHidden
    >
      {children}
    </Modal>
  );
}
