import { Container, getContainer } from "@cloudflare/containers";

// D2 (2026-07-31): Worker의 시크릿(wrangler secret put)을 컨테이너 프로세스의 환경변수로
// 그대로 흘려보낸다 -- uvicorn 쪽 app.config.Settings가 이미 환경변수로 읽게 돼 있어서
// (.env와 동일 계약, app/config.py 참고) 컨테이너 안에서는 로컬 실행과 다를 게 없다.
//   WHY: 시크릿을 Dockerfile이나 이미지에 굽지 않고, 실행 시점에만 주입한다 -- 이미지
//     자체는 시크릿 없이도 빌드/재사용 가능하다.
//   COST: 시크릿 이름이 바뀌면 여기와 `wrangler secret put` 양쪽을 같이 고쳐야 한다.
//   EXIT: 시크릿이 늘어나면 이 객체에 한 줄만 추가.
export class CodemapContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "10m"; // 유휴 10분 후 종료 -- 상시 대기 과금 대신 콜드스타트를 감수한다
  enableInternet = true; // NVIDIA API 호출 + GITHUB_URL의 git clone에 필요(기본값이지만 명시)

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = {
      NVIDIA_API_KEY: env.NVIDIA_API_KEY,
      INTERNAL_API_KEY: env.INTERNAL_API_KEY,
      APP_ENV: "production",
      ENGINE_MODE: "codemap",
      // D4 (2026-07-31, 실측 교체): z-ai/glm-5.2가 NVIDIA 쪽에서 무응답(90초+ 타임아웃,
      // 직접 호출로 확인)이라 기본값으로 못 쓴다. mistralai/mistral-medium-3.5-128b로
      // 교체 -- 같은 세션에서 실제 배포본에 analysis_doc+diagram 둘 다 완전 성공
      // (21.8s/7.6s) 확인. 비교 대상 nvidia/nemotron-3-ultra-550b-a55b도 성공은
      // 했지만 콜당 ~80초라 기본값에는 너무 느림(4분+/job) -- 그쪽은 필요할 때
      // modelCode로 명시해서 쓴다.
      DEFAULT_MODEL_CODE: "mistralai/mistral-medium-3.5-128b",
    };
  }
}

export default {
  async fetch(request, env) {
    // D3: 세션/사용자별로 컨테이너를 나눌 이유가 없다(이 서비스는 job_id 기반 인메모리
    // 저장소를 이미 갖고 있음, app/jobs.py) -- 인스턴스 하나를 계속 재사용한다.
    const container = getContainer(env.CODEMAP_CONTAINER);
    return container.fetch(request);
  },
};
