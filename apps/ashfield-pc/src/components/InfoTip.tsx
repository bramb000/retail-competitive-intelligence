interface InfoTipProps {
  /** Short plain-language explanation for non-technical users */
  plain: string;
  /** Optional anchor in Methods wiki (without #) */
  methodsId?: string;
  onOpenMethods?: (sectionId?: string) => void;
}

/** Hover/focus tip; optional link into Methods wiki for full formulas. */
export function InfoTip({ plain, methodsId, onOpenMethods }: InfoTipProps) {
  return (
    <span className="info-tip">
      <button
        type="button"
        className="info-tip-btn"
        title={plain}
        aria-label={plain}
      >
        ?
      </button>
      <span className="info-tip-bubble" role="tooltip">
        <span className="info-tip-plain">{plain}</span>
        {onOpenMethods ? (
          <button
            type="button"
            className="info-tip-link"
            onClick={() => onOpenMethods(methodsId)}
          >
            Full methods →
          </button>
        ) : null}
      </span>
    </span>
  );
}
