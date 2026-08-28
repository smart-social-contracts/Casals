<script lang="ts">
  import { footerCopy, isLocalDeployment } from '$lib/buildIdentity';

  const version = typeof __BUILD_VERSION__ !== 'undefined' ? __BUILD_VERSION__ : '';
  const commit = typeof __BUILD_COMMIT__ !== 'undefined' ? __BUILD_COMMIT__ : '';
  const datetime = typeof __BUILD_TIME__ !== 'undefined' ? __BUILD_TIME__ : '';
  const local =
    typeof window !== 'undefined' && isLocalDeployment(window.location.hostname);
  const copy = footerCopy({ version, commit, datetime, local });
</script>

<footer class="border-t border-[var(--color-border-primary)]">
  <div class="w-full px-4 sm:px-6 py-4">
    <p class="text-xs text-primary-400 text-center font-mono" data-testid="build-footer">
      {copy}
    </p>
  </div>
</footer>
