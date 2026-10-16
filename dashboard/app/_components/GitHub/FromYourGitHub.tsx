/** "From your GitHub": on a find or a part that came from the owner's own
 *  GitHub projects (githubService.fromYourGitHub). */
export default function FromYourGitHub() {
  return (
    <span
      data-from-your-github
      style={{
        fontFamily: "var(--font-nunito), sans-serif",
        fontWeight: 700,
        fontSize: 11,
        lineHeight: 1,
        padding: "3px 8px",
        borderRadius: 999,
        border: "1px solid var(--sky)",
        color: "var(--sky)",
        whiteSpace: "nowrap",
        alignSelf: "center",
      }}
    >
      From your GitHub
    </span>
  );
}
